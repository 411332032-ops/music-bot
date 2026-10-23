"""Discord 這一層：收訊息、貼符號、排隊、回覆、開機補處理（architecture.md 第 4、7 節）。

BotHandler 是邏輯（不直接連 Discord，所以可以用假物件測試）；
MusicBotClient 是很薄的一層，把 Discord 事件轉給 BotHandler。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord

from .models import Outcome
from .pipeline import MAX_IMAGE_BYTES, Processor, failure_outcome
from .queue_worker import DEFAULT_MAX_PER_USER, DEFAULT_MAX_TOTAL, JobQueue
from .replies import compose_rejection, compose_reply, result_symbol

logger = logging.getLogger(__name__)

HOURGLASS = "⏳"
RESULT_SYMBOLS = ("✅", "🚫", "❌")  # 固定順序，貼符號時依這個順序
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp")
_NORMAL_MESSAGE_TYPES = (discord.MessageType.default, discord.MessageType.reply)

# 回覆只准 @ 到被回覆的那個人：就算歌詞或名稱裡有 @everyone，也不會通知到別人
_REPLY_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=False, replied_user=True)


def _is_image(attachment: Any) -> bool:
    content_type = (getattr(attachment, "content_type", None) or "").lower()
    name = (getattr(attachment, "filename", "") or "").lower()
    return content_type.startswith("image/") or name.endswith(_IMAGE_EXTS)


def _local_naive(moment: datetime) -> datetime:
    """Discord 給的是 UTC 時間；Excel 與檔名用這台電腦的本地時間。"""
    return moment.astimezone().replace(tzinfo=None)


@dataclass
class _Job:
    message: Any
    attachment: Any


@dataclass
class _MessageState:
    remaining: int = 0  # 這則訊息還有幾張圖在排隊或處理中
    symbols: set[str] = field(default_factory=set)
    sealed: bool = False  # 所有附件都已經送進（或被拒絕於）隊伍
    finalized: bool = False


class BotHandler:
    def __init__(
        self,
        processor: Processor,
        channel_id: int,
        bot_user: Any,
        max_total: int = DEFAULT_MAX_TOTAL,
        max_per_user: int = DEFAULT_MAX_PER_USER,
    ) -> None:
        self.processor = processor
        self.channel_id = channel_id
        self.bot_user = bot_user
        self.queue: JobQueue[_Job] = JobQueue(self._process_job, max_total, max_per_user)
        self._states: dict[int, _MessageState] = {}
        self._deleted: set[int] = set()

    # ---------- 生命週期 ----------

    def start(self) -> None:
        self.queue.start()

    async def stop(self) -> None:
        await self.queue.stop()

    def mark_deleted(self, message_id: int) -> None:
        # 只記錄我們正在處理的訊息；其他頻道、其他訊息的刪除與我們無關，記下來只會一直累積
        if message_id in self._states:
            self._deleted.add(message_id)

    # ---------- 收訊息 ----------

    def _images_or_reason(self, message: Any) -> tuple[list[Any], str | None]:
        """回傳 (要處理的圖片, 略過的原因)。有圖片時原因是 None。"""
        if message.channel.id != self.channel_id:  # 只聽指定的那一個頻道
            return [], f"不是指定的頻道（訊息來自頻道 {message.channel.id}，設定的 CHANNEL_ID 是 {self.channel_id}）"
        if message.author.bot:  # 包含自己與其他機器人
            return [], "機器人發的訊息"
        if message.type not in _NORMAL_MESSAGE_TYPES:
            return [], f"系統訊息（{message.type.name}）"
        images = [a for a in message.attachments if _is_image(a)]
        if not images:
            kinds = [getattr(a, "content_type", None) or getattr(a, "filename", "?") for a in message.attachments]
            return [], f"沒有圖片附件（附件 {len(kinds)} 個：{kinds}）"
        return images, None

    async def handle_message(self, message: Any, enforce_limits: bool = True) -> None:
        images, reason = self._images_or_reason(message)
        if not images:
            # 只記原因，不記訊息內容。排查「為什麼沒反應」時很重要
            logger.info("略過訊息 %s：%s", message.id, reason)
            return
        if message.id in self._states:
            # 開機補處理掃歷史的同時，這則剛好也被即時收到並排進去了：不能再處理第二次
            logger.info("訊息 %s 已經在處理中，略過", message.id)
            return
        logger.info("收到圖片訊息 %s：%d 張圖", message.id, len(images))

        # 先貼 ⏳ 再排隊，這樣排在後面的人也馬上看得到「我被收到了」
        await self._safe(message.add_reaction(HOURGLASS))
        state = _MessageState()
        self._states[message.id] = state

        rejected = False
        for attachment in images:
            if self.queue.submit(str(message.author.id), _Job(message, attachment), enforce_limits):
                state.remaining += 1
            else:
                rejected = True
        state.sealed = True

        if rejected:
            state.symbols.add("❌")
            await self._reply(message, compose_rejection())
        if state.remaining == 0:
            await self._finalize(message, state)

    # ---------- 處理一張（由佇列的工作員呼叫） ----------

    async def _process_job(self, job: _Job) -> None:
        message, attachment = job.message, job.attachment
        symbol: str | None = None

        if message.id in self._deleted:
            logger.info("原訊息 %s 已被刪除，略過這一張", message.id)
        else:
            symbol = await self._process_and_reply(message, attachment)
        await self._finish_one(message, symbol)

    async def _process_and_reply(self, message: Any, attachment: Any) -> str:
        image: bytes | None = None
        failure: str | None = None
        if getattr(attachment, "size", 0) > MAX_IMAGE_BYTES:
            failure = "圖片過大"  # 太大就不下載
        else:
            try:
                image = await attachment.read()
            except Exception:
                logger.exception("下載附件失敗")
                failure = "下載失敗"

        try:
            result = await self.processor.process(
                image,
                uploader=message.author.display_name,  # 伺服器暱稱 → 顯示名稱 → 帳號名稱
                taken_at=_local_naive(message.created_at),
                caption=message.content or None,
                failure=failure,
            )
            text = compose_reply(result.outcome, result.daily_count)
            symbol = result_symbol(result.outcome)
        except Exception:
            # Processor 設計上不丟例外；真的丟了也不能讓這張沒有任何回應
            logger.exception("處理圖片時發生未預期的錯誤")
            text = "❌ 分析失敗，請再傳一次（原因：內部錯誤）"
            symbol = result_symbol(failure_outcome("內部錯誤"))

        await self._reply(message, text)
        return symbol

    async def _finish_one(self, message: Any, symbol: str | None) -> None:
        state = self._states.get(message.id)
        if state is None:
            return
        state.remaining -= 1
        if symbol:
            state.symbols.add(symbol)
        if state.sealed and state.remaining <= 0:
            await self._finalize(message, state)

    async def _finalize(self, message: Any, state: _MessageState) -> None:
        """這則訊息的圖都處理完：拿掉 ⏳，貼上結果符號。"""
        if state.finalized:
            return
        state.finalized = True
        self._states.pop(message.id, None)
        self._deleted.discard(message.id)
        await self._safe(message.remove_reaction(HOURGLASS, self.bot_user))
        for symbol in RESULT_SYMBOLS:
            if symbol in state.symbols:
                await self._safe(message.add_reaction(symbol))

    # ---------- Discord 呼叫的保護殼 ----------

    async def _reply(self, message: Any, text: str) -> None:
        await self._safe(message.reply(text, allowed_mentions=_REPLY_MENTIONS))

    @staticmethod
    async def _safe(awaitable: Any) -> None:
        """Discord 的呼叫（回覆、貼符號）失敗，只記 log，不影響後面的流程。"""
        try:
            await awaitable
        except Exception:
            logger.exception("呼叫 Discord 失敗")

    # ---------- 開機補處理 ----------

    def _has_our_result_reaction(self, message: Any) -> bool:
        return any(getattr(r, "me", False) and str(r.emoji) in RESULT_SYMBOLS for r in message.reactions)

    async def backfill(self, channel: Any, initialized_at: datetime) -> int:
        """補處理關機期間漏掉的圖片。

        由新往舊找，找到「第一則有機器人結果符號的訊息」或「比第一次啟動更早的訊息」就停。
        中間含圖片、但沒有結果符號（或只有卡住的 ⏳）的，就是漏掉的。
        """
        missed: list[Any] = []
        async for message in channel.history(limit=None):
            if message.created_at < initialized_at:
                break
            if self._has_our_result_reaction(message):
                break
            if self._images_or_reason(message)[0]:
                missed.append(message)

        if missed:
            logger.info("開機補處理：找到 %d 則漏掉的圖片訊息", len(missed))
        for message in reversed(missed):  # 依原本的上傳順序；積壓的圖不受佇列上限限制
            await self.handle_message(message, enforce_limits=False)
        return len(missed)


# ---------- 第一次啟動的記號（state.json） ----------


def load_initialized_at(path: Path) -> datetime | None:
    """讀「第一次啟動」的時間。沒有檔案、或檔案壞掉，都當作第一次啟動。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["initialized_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_initialized_at(path: Path, moment: datetime) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"initialized_at": moment.isoformat()}), encoding="utf-8")


# ---------- 連 Discord 的薄薄一層 ----------


class MusicBotClient(discord.Client):
    def __init__(self, processor: Processor, channel_id: int, state_path: Path) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # 需要在 Developer Portal 同步打開 Message Content Intent
        super().__init__(intents=intents)
        self._processor = processor
        self._channel_id = channel_id
        self._state_path = state_path
        self.handler: BotHandler | None = None

    async def on_ready(self) -> None:
        if self.handler is not None:  # 斷線重連會再觸發 on_ready，只需要初始化一次
            return
        self.handler = BotHandler(self._processor, self._channel_id, self.user)
        self.handler.start()
        logger.info("已上線：%s，只聽頻道 %s", self.user, self._channel_id)

        channel = self.get_channel(self._channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self._channel_id)
            except discord.DiscordException:
                logger.error("找不到頻道 %s：請確認 CHANNEL_ID 正確，且機器人在那個頻道有「檢視頻道」權限", self._channel_id)
                return

        initialized_at = load_initialized_at(self._state_path)
        if initialized_at is None:
            save_initialized_at(self._state_path, datetime.now(timezone.utc))
            logger.info("第一次啟動：不補處理頻道裡的舊圖片，從現在開始處理")
            return
        try:
            await self.handler.backfill(channel, initialized_at)
        except Exception:
            logger.exception("開機補處理失敗（不影響之後新進來的圖片）")

    async def on_message(self, message: discord.Message) -> None:
        if self.handler is not None:
            await self.handler.handle_message(message)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        # 用 raw 事件：補處理從歷史抓回來的舊訊息不在快取裡，一般的 on_message_delete 收不到
        if self.handler is not None:
            self.handler.mark_deleted(payload.message_id)

    async def close(self) -> None:
        if self.handler is not None:
            await self.handler.stop()
        await super().close()
