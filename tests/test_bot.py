"""bot.py 的邏輯層：收訊息、貼符號、排隊、回覆、開機補處理（architecture.md 第 4、7 節）。

用假的訊息／附件／頻道物件，不連真的 Discord，也不呼叫 claude。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from music_bot.bot import BotHandler, load_initialized_at, save_initialized_at
from music_bot.models import Outcome
from music_bot.pipeline import MAX_IMAGE_BYTES, ProcessResult

CHANNEL = 111
BOT_USER = SimpleNamespace(id=999, bot=True, display_name="Music-Recognition-Bot")
T0 = datetime(2026, 10, 21, 6, 3, 22, tzinfo=timezone.utc)


# ---------- 假物件 ----------


class FakeAttachment:
    def __init__(self, filename="cover.jpg", content_type="image/jpeg", size=1000, data=b"IMG", error=None):
        self.filename, self.content_type, self.size = filename, content_type, size
        self._data, self._error = data, error
        self.reads = 0

    async def read(self):
        self.reads += 1
        if self._error:
            raise self._error
        return self._data


class FakeMessage:
    _next_id = 1000

    def __init__(
        self,
        attachments=None,
        channel_id=CHANNEL,
        author=None,
        content="",
        created_at=T0,
        reactions=None,
        mtype=discord.MessageType.default,
        reply_error=None,
    ):
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.channel = SimpleNamespace(id=channel_id)
        self.author = author or SimpleNamespace(id=1, bot=False, display_name="小明")
        self.attachments = attachments if attachments is not None else [FakeAttachment()]
        self.content = content
        self.created_at = created_at
        self.reactions = reactions or []
        self.type = mtype
        self.events = []
        self._reply_error = reply_error

    async def add_reaction(self, emoji):
        self.events.append(("add", emoji))

    async def remove_reaction(self, emoji, member):
        self.events.append(("remove", emoji))

    async def reply(self, content, **kwargs):
        if self._reply_error:
            raise self._reply_error
        self.events.append(("reply", content, kwargs))

    def adds(self):
        return [e[1] for e in self.events if e[0] == "add"]

    def replies(self):
        return [e for e in self.events if e[0] == "reply"]


def music_outcome(**kw):
    base = dict(image_type="專輯封面", title="某專輯", artist="某歌手", genre=["Pop"], lyrics="", confidence="高")
    base.update(kw)
    return Outcome(**base)


class FakeProcessor:
    def __init__(self, outcome=None, count=1):
        self.outcome = outcome or music_outcome()
        self.count = count
        self.calls = []
        self.error = None

    async def process(self, image, uploader, taken_at, caption=None, failure=None):
        self.calls.append(
            {"image": image, "uploader": uploader, "taken_at": taken_at, "caption": caption, "failure": failure}
        )
        if self.error:
            raise self.error
        return ProcessResult(self.outcome, self.count)


async def make(processor=None, **kw):
    proc = processor or FakeProcessor()
    h = BotHandler(proc, channel_id=CHANNEL, bot_user=BOT_USER, **kw)
    h.start()
    return h, proc


async def run(h, *messages):
    for m in messages:
        await h.handle_message(m)
    await h.queue.join()
    await h.stop()


# ---------- 什麼訊息要理、什麼不理 ----------


async def test_messages_in_other_channels_are_ignored():
    h, proc = await make()
    m = FakeMessage(channel_id=222)
    await run(h, m)
    assert proc.calls == [] and m.events == []


async def test_messages_from_bots_including_ourselves_are_ignored():
    h, proc = await make()
    other_bot = FakeMessage(author=SimpleNamespace(id=5, bot=True, display_name="b"))
    ours = FakeMessage(author=BOT_USER)
    await run(h, other_bot, ours)
    assert proc.calls == [] and other_bot.events == [] and ours.events == []


async def test_text_only_messages_are_ignored():
    h, proc = await make()
    m = FakeMessage(attachments=[], content="大家好")
    await run(h, m)
    assert proc.calls == [] and m.events == []


async def test_non_image_attachments_are_ignored():
    h, proc = await make()
    m = FakeMessage(attachments=[FakeAttachment("notes.txt", "text/plain")])
    await run(h, m)
    assert proc.calls == [] and m.events == []


async def test_system_messages_are_ignored():
    h, proc = await make()
    m = FakeMessage(mtype=discord.MessageType.pins_add)
    await run(h, m)
    assert proc.calls == []


async def test_image_without_content_type_is_recognised_by_extension():
    h, proc = await make()
    m = FakeMessage(attachments=[FakeAttachment("a.PNG", None)])
    await run(h, m)
    assert len(proc.calls) == 1


# ---------- 一般流程 ----------


async def test_hourglass_first_then_reply_then_result_symbol():
    h, proc = await make()
    m = FakeMessage()
    await run(h, m)
    kinds = [e[:2] if e[0] != "reply" else ("reply",) for e in m.events]
    assert kinds == [("add", "⏳"), ("reply",), ("remove", "⏳"), ("add", "✅")]


async def test_hourglass_is_added_before_anything_is_processed():
    h, proc = await make()
    seen_when_processing = []
    m = FakeMessage()
    original = proc.process

    async def spy(*a, **k):
        seen_when_processing.append(list(m.events))
        return await original(*a, **k)

    proc.process = spy
    await run(h, m)
    assert seen_when_processing[0] == [("add", "⏳")]


async def test_reply_is_a_reply_to_the_message_pinging_only_the_uploader():
    h, _ = await make(FakeProcessor(count=7))
    m = FakeMessage()
    await run(h, m)
    (_, content, kwargs) = m.replies()[0]
    assert content.splitlines()[-1] == "📊 今天已紀錄音樂數：7"
    mentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False and mentions.roles is False and mentions.users is False
    assert mentions.replied_user is True


async def test_processor_receives_uploader_time_and_caption():
    h, proc = await make()
    m = FakeMessage(content="這是某張專輯", author=SimpleNamespace(id=1, bot=False, display_name="小明暱稱"))
    await run(h, m)
    call = proc.calls[0]
    assert call["image"] == b"IMG"
    assert call["uploader"] == "小明暱稱"
    assert call["caption"] == "這是某張專輯"
    assert call["taken_at"] == T0.astimezone().replace(tzinfo=None)
    assert call["failure"] is None


async def test_empty_message_text_is_passed_as_none():
    h, proc = await make()
    await run(h, FakeMessage(content=""))
    assert proc.calls[0]["caption"] is None


@pytest.mark.parametrize(
    "outcome, symbol",
    [
        (music_outcome(), "✅"),
        (music_outcome(image_type="非音樂相關", title="手搖飲料", artist="無", genre=[], confidence="無"), "🚫"),
        (
            Outcome("處理失敗", "無法判斷圖片內容", "無", [], "逾時", "無", failure_reason="逾時"),
            "❌",
        ),
    ],
)
async def test_result_symbol_matches_the_outcome(outcome, symbol):
    h, _ = await make(FakeProcessor(outcome))
    m = FakeMessage()
    await run(h, m)
    assert m.adds() == ["⏳", symbol]
    assert ("remove", "⏳") in m.events


# ---------- 多張圖 ----------


async def test_each_image_in_one_message_gets_its_own_reply_and_one_final_reaction_set():
    h, proc = await make()
    m = FakeMessage(attachments=[FakeAttachment(f"{i}.jpg") for i in range(3)])
    await run(h, m)
    assert len(proc.calls) == 3
    assert len(m.replies()) == 3
    assert m.events.count(("remove", "⏳")) == 1
    assert m.adds().count("⏳") == 1
    assert m.adds().count("✅") == 1  # 同樣的結果符號只貼一次


async def test_mixed_results_in_one_message_show_every_kind_of_symbol():
    h, proc = await make()
    results = iter(
        [
            ProcessResult(music_outcome(), 1),
            ProcessResult(music_outcome(image_type="非音樂相關", title="x", artist="無", genre=[], confidence="無"), 1),
        ]
    )

    async def process(*a, **k):
        return next(results)

    proc.process = process
    m = FakeMessage(attachments=[FakeAttachment("a.jpg"), FakeAttachment("b.jpg")])
    await run(h, m)
    assert set(m.adds()) == {"⏳", "✅", "🚫"}


# ---------- 下載與大小 ----------


async def test_oversized_attachment_is_not_downloaded():
    h, proc = await make()
    big = FakeAttachment(size=MAX_IMAGE_BYTES + 1)
    await run(h, FakeMessage(attachments=[big]))
    assert big.reads == 0
    assert proc.calls[0]["image"] is None
    assert proc.calls[0]["failure"] == "圖片過大"


async def test_download_error_is_reported_as_download_failure():
    h, proc = await make()
    await run(h, FakeMessage(attachments=[FakeAttachment(error=OSError("cdn 404"))]))
    assert proc.calls[0]["image"] is None
    assert proc.calls[0]["failure"] == "下載失敗"


# ---------- 排隊上限 ----------


async def test_queue_full_gets_a_rejection_reply_and_cross_mark_without_processing():
    h, proc = await make(max_total=1, max_per_user=99)
    first, second = FakeMessage(), FakeMessage()
    await h.handle_message(first)
    await h.handle_message(second)  # 第一張還在排隊，第二張被拒絕
    await h.queue.join()
    await h.stop()
    assert len(proc.calls) == 1
    assert "排隊" in second.replies()[0][1]
    assert second.adds() == ["⏳", "❌"]
    assert ("remove", "⏳") in second.events


async def test_per_user_limit_applies_to_the_same_author():
    h, proc = await make(max_total=99, max_per_user=1)
    alice = SimpleNamespace(id=1, bot=False, display_name="alice")
    bob = SimpleNamespace(id=2, bot=False, display_name="bob")
    m1, m2, m3 = FakeMessage(author=alice), FakeMessage(author=alice), FakeMessage(author=bob)
    for m in (m1, m2, m3):
        await h.handle_message(m)
    await h.queue.join()
    await h.stop()
    assert len(proc.calls) == 2  # alice 的第二張被拒絕
    assert "排隊" in m2.replies()[0][1]


async def test_partially_rejected_multi_image_message_still_processes_the_accepted_ones():
    h, proc = await make(max_total=2, max_per_user=99)
    m = FakeMessage(attachments=[FakeAttachment(f"{i}.jpg") for i in range(3)])
    await run(h, m)
    assert len(proc.calls) == 2
    assert "❌" in m.adds() and "✅" in m.adds()
    assert m.events.count(("remove", "⏳")) == 1


# ---------- 出錯也不能掛 ----------


async def test_reply_failure_is_swallowed_and_reactions_still_finish(caplog):
    h, _ = await make()
    m = FakeMessage(reply_error=discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom"))
    with caplog.at_level(logging.ERROR):
        await run(h, m)
    assert m.adds() == ["⏳", "✅"]


async def test_unexpected_processor_error_becomes_a_failure_reply_and_cross_mark():
    proc = FakeProcessor()
    proc.error = RuntimeError("boom")
    h, _ = await make(proc)
    m = FakeMessage()
    await run(h, m)
    assert m.adds() == ["⏳", "❌"]
    assert "請再傳一次" in m.replies()[0][1]


async def test_deleted_message_is_skipped_without_processing_or_reply():
    h, proc = await make()
    m = FakeMessage()
    await h.handle_message(m)
    h.mark_deleted(m.id)
    await h.queue.join()
    await h.stop()
    assert proc.calls == []
    assert m.replies() == []


# ---------- 開機補處理 ----------


class FakeChannel:
    def __init__(self, *messages):
        self.messages = sorted(messages, key=lambda m: m.created_at, reverse=True)  # 新的在前

    async def history(self, limit=None):
        for m in self.messages:
            yield m


def at(minutes):
    return T0 + timedelta(minutes=minutes)


async def test_backfill_processes_missed_images_oldest_first_until_the_last_processed_one():
    done = FakeMessage(created_at=at(1), reactions=[SimpleNamespace(emoji="✅", me=True)])
    missed1 = FakeMessage(created_at=at(2))
    chat = FakeMessage(created_at=at(3), attachments=[], content="哈囉")
    missed2 = FakeMessage(created_at=at(4))
    older_unprocessed = FakeMessage(created_at=at(0))  # 比 done 更舊，不該被碰
    h, proc = await make()
    n = await h.backfill(FakeChannel(older_unprocessed, done, missed1, chat, missed2), initialized_at=at(-10))
    await h.queue.join()
    await h.stop()
    assert n == 2
    assert len(proc.calls) == 2
    assert missed1.replies() and missed2.replies()
    assert not done.events and not older_unprocessed.events and not chat.events
    # 依原本的上傳順序處理
    assert [c["taken_at"] for c in proc.calls] == [
        missed1.created_at.astimezone().replace(tzinfo=None),
        missed2.created_at.astimezone().replace(tzinfo=None),
    ]


async def test_backfill_ignores_images_older_than_the_first_start():
    old = FakeMessage(created_at=at(-30))
    new = FakeMessage(created_at=at(5))
    h, proc = await make()
    n = await h.backfill(FakeChannel(old, new), initialized_at=at(0))
    await h.queue.join()
    await h.stop()
    assert n == 1 and old.events == []


async def test_backfill_reprocesses_a_message_stuck_on_hourglass():
    stuck = FakeMessage(created_at=at(2), reactions=[SimpleNamespace(emoji="⏳", me=True)])
    h, proc = await make()
    await h.backfill(FakeChannel(stuck), initialized_at=at(0))
    await h.queue.join()
    await h.stop()
    assert len(proc.calls) == 1
    assert ("remove", "⏳") in stuck.events and "✅" in stuck.adds()


async def test_backfill_skips_other_bots_and_ignores_reactions_by_others():
    bot_msg = FakeMessage(created_at=at(1), author=SimpleNamespace(id=5, bot=True, display_name="b"))
    someone_elses_check = FakeMessage(created_at=at(2), reactions=[SimpleNamespace(emoji="✅", me=False)])
    h, proc = await make()
    n = await h.backfill(FakeChannel(bot_msg, someone_elses_check), initialized_at=at(0))
    await h.queue.join()
    await h.stop()
    assert n == 1  # 別人貼的 ✅ 不算「已處理」
    assert bot_msg.events == []


async def test_backfill_with_nothing_to_do_returns_zero():
    h, proc = await make()
    assert await h.backfill(FakeChannel(), initialized_at=at(0)) == 0
    await h.stop()
    assert proc.calls == []


# ---------- state.json ----------


def test_first_start_has_no_state(tmp_path):
    assert load_initialized_at(tmp_path / "state.json") is None


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    save_initialized_at(path, T0)
    assert load_initialized_at(path) == T0
    assert json.loads(path.read_text(encoding="utf-8"))["initialized_at"]


def test_corrupt_state_is_treated_as_first_start(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_initialized_at(path) is None


# ---------- 診斷用 log：為什麼處理、為什麼略過（不記訊息內容） ----------


async def test_ignored_message_in_other_channel_logs_the_channel_id(caplog):
    h, _ = await make()
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await run(h, FakeMessage(channel_id=222))
    assert any("略過" in m and "222" in m and "頻道" in m for m in caplog.messages)


async def test_text_only_message_logs_that_there_is_no_image(caplog):
    h, _ = await make()
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await run(h, FakeMessage(attachments=[], content="秘密的內容不該出現在 log"))
    assert any("略過" in m and "沒有圖片" in m for m in caplog.messages)
    assert not any("秘密的內容" in m for m in caplog.messages)


async def test_non_image_attachment_logs_its_type(caplog):
    h, _ = await make()
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await run(h, FakeMessage(attachments=[FakeAttachment("a.txt", "text/plain")]))
    assert any("略過" in m and "text/plain" in m for m in caplog.messages)


async def test_bot_message_is_logged_as_skipped(caplog):
    h, _ = await make()
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await run(h, FakeMessage(author=SimpleNamespace(id=5, bot=True, display_name="b")))
    assert any("略過" in m and "機器人" in m for m in caplog.messages)


async def test_accepted_message_is_logged_with_image_count(caplog):
    h, _ = await make()
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await run(h, FakeMessage(attachments=[FakeAttachment("a.jpg"), FakeAttachment("b.jpg")]))
    assert any("收到" in m and "2" in m for m in caplog.messages)


# ---------- 補處理的細節：不受佇列上限、不重複處理 ----------


async def test_backfill_is_not_rejected_by_the_queue_limits():
    """關機期間積壓的圖，補處理時不能被「排隊太多」擋掉然後永遠補不回來。"""
    msgs = [FakeMessage(created_at=at(i + 1)) for i in range(4)]  # 同一個人的 4 則
    h, proc = await make(max_total=3, max_per_user=2)
    n = await h.backfill(FakeChannel(*msgs), initialized_at=at(0))
    await h.queue.join()
    await h.stop()
    assert n == 4 and len(proc.calls) == 4
    for m in msgs:
        assert "❌" not in m.adds()
        assert "✅" in m.adds()


async def test_live_messages_are_still_limited_after_the_backfill_change():
    h, proc = await make(max_total=1, max_per_user=1)
    first, second = FakeMessage(), FakeMessage()
    await h.handle_message(first)
    await h.handle_message(second)
    await h.queue.join()
    await h.stop()
    assert "❌" in second.adds()


async def test_backfill_does_not_process_a_message_the_live_handler_already_queued():
    m = FakeMessage(created_at=at(2))
    h, proc = await make()
    await h.handle_message(m)  # 補處理掃歷史的同時，這則剛好也被即時收到了
    await h.backfill(FakeChannel(m), initialized_at=at(0))
    await h.queue.join()
    await h.stop()
    assert len(proc.calls) == 1
    assert len(m.replies()) == 1


# ---------- 被刪除的訊息：不能一直累積記憶體 ----------


async def test_mark_deleted_ignores_messages_we_are_not_tracking():
    h, _ = await make()
    h.mark_deleted(123456)
    await h.stop()
    assert h._deleted == set()


async def test_deleted_mark_is_dropped_once_the_message_is_finished():
    h, proc = await make()
    m = FakeMessage()
    await h.handle_message(m)
    h.mark_deleted(m.id)
    await h.queue.join()
    await h.stop()
    assert proc.calls == []
    assert h._deleted == set()


# ---------- log：收到、排隊、處理、完成都要有紀錄（不記訊息內容與暱稱） ----------


async def test_queueing_processing_and_finishing_are_logged(caplog):
    h, _ = await make()
    m = FakeMessage(content="秘密內容", attachments=[FakeAttachment(size=2048)])
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await run(h, m)
    text = "\n".join(caplog.messages)
    assert f"已排入佇列" in text and str(m.id) in text
    assert f"開始處理訊息 {m.id}" in text and "2048 bytes" in text
    assert f"處理完成 訊息 {m.id}" in text and "✅" in text and "秒" in text
    assert "秘密內容" not in text and "小明" not in text


async def test_rejected_job_is_logged_as_a_warning(caplog):
    h, _ = await make(max_total=1, max_per_user=1)
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await h.handle_message(FakeMessage())
        second = FakeMessage()
        await h.handle_message(second)
        await h.queue.join()
        await h.stop()
    assert any(r.levelno == logging.WARNING and "拒絕" in r.getMessage() and str(second.id) in r.getMessage()
               for r in caplog.records)


async def test_message_finalized_is_logged_with_its_symbols(caplog):
    h, _ = await make()
    m = FakeMessage()
    with caplog.at_level(logging.INFO, logger="music_bot.bot"):
        await run(h, m)
    assert any(f"訊息 {m.id}" in msg and "全部處理完" in msg and "✅" in msg for msg in caplog.messages)
