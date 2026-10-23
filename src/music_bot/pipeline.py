"""處理一張圖的完整流程：檢查 → 分析 → 存照片 → 寫 Excel → 準備回覆內容。

這裡完全不碰 Discord，所以可以單獨測試。任何一步出問題都轉成「處理失敗」的結果，
不會丟出例外（architecture.md 第 4 節）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import storage
from .analyzer import AnalysisFailure
from .models import Outcome
from .schema import FAILURE_TYPE, LYRICS_TYPE, NON_MUSIC_TYPE, NONE_WORD, UNKNOWN, Analysis, is_unknown_name
from .storage import ExcelStore, Record

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
UNKNOWN_CONTENT = "處理失敗，無法判斷圖片內容"
FAILED_NAME = "處理失敗"  # 失敗又沒有內容可當檔名簡稱時用


class AnalyzerLike(Protocol):
    async def analyze(self, image: bytes, ext: str, caption: str | None = None) -> Analysis: ...


@dataclass(frozen=True)
class ProcessResult:
    outcome: Outcome
    daily_count: int


def failure_outcome(reason: str, content: str | None = None) -> Outcome:
    """降級用的結果。content 是「有拿到的圖片內容」，沒有就用固定文字。"""
    return Outcome(
        image_type=FAILURE_TYPE,
        title=content or UNKNOWN_CONTENT,
        artist=NONE_WORD,
        genre=[],
        lyrics=reason,
        confidence=NONE_WORD,
        failure_reason=reason,
        album_title=NONE_WORD,
        release_type=NONE_WORD,
    )


def outcome_from_analysis(a: Analysis) -> Outcome:
    if a.type == NON_MUSIC_TYPE:
        # 非音樂：歌曲名稱欄＝真實物品說明，歌詞/文字欄＝較完整的內容說明；
        # 沒有歌手、曲風、專輯、發行類型、信心程度（模型就算亂填也不採用）
        return Outcome(
            NON_MUSIC_TYPE,
            a.title,
            NONE_WORD,
            [],
            a.lyrics_text,
            NONE_WORD,
            album_title=NONE_WORD,
            release_type=NONE_WORD,
        )
    # 歌詞轉錄功能已停用：音樂相關的圖，歌詞/文字欄一律留空。
    # 不只靠提示詞要求模型不要寫，這裡也強制清掉，模型就算多給了也不採用。
    # 音樂相關的圖，發行類型不可能是「無」；模型這樣回就當作不明。
    release_type = UNKNOWN if a.release_type == NONE_WORD else a.release_type
    # 搜尋用的那一小句，只在「歌詞截圖、而且歌名不明」時才留下；其他情況模型就算給了也丟掉
    snippet = a.lyric_snippet if (a.type == LYRICS_TYPE and is_unknown_name(a.title)) else ""
    return Outcome(
        a.type,
        a.title,
        a.artist,
        list(a.genre_estimate),
        "",
        a.confidence,
        album_title=a.album_title,
        release_type=release_type,
        lyric_snippet=snippet,
    )


def file_name_hint(outcome: Outcome) -> str:
    """照片檔名用的簡稱：有歌名就用歌名，沒有就用專輯名稱，都沒有就是「不明」。"""
    if outcome.is_music:
        for name in (outcome.title, outcome.album_title):
            if not is_unknown_name(name):
                return name
        return UNKNOWN
    return outcome.title


class Processor:
    def __init__(self, analyzer: AnalyzerLike, store: ExcelStore, data_dir: Path, photos_dir: Path) -> None:
        self.analyzer = analyzer
        self.store = store
        self.data_dir = Path(data_dir)
        self.photos_dir = Path(photos_dir)

    async def process(
        self,
        image: bytes | None,
        uploader: str,
        taken_at: datetime,
        caption: str | None = None,
        failure: str | None = None,
    ) -> ProcessResult:
        """taken_at 是上傳時間（補處理時也是原本的上傳時間）。

        image 為 None 代表沒有拿到圖：failure 若有給（例如附件太大所以沒下載），
        就用它當失敗原因；沒給就當作下載失敗。
        """
        ext: str | None = None
        short_name = FAILED_NAME
        keep_photo = image is not None and len(image) <= MAX_IMAGE_BYTES

        if image is None:
            outcome = failure_outcome(failure or "下載失敗")
        elif not keep_photo:
            outcome = failure_outcome("圖片過大")
        else:
            ext = storage.detect_image_ext(image)
            if ext is None:
                outcome = failure_outcome("圖片損壞或格式不支援")
            else:
                try:
                    outcome = outcome_from_analysis(await self.analyzer.analyze(image, ext, caption))
                    short_name = file_name_hint(outcome)
                except AnalysisFailure as e:
                    outcome = failure_outcome(e.reason, e.salvaged_title)
                    short_name = e.salvaged_title or FAILED_NAME

        photo_rel: str | None = None
        if keep_photo:
            assert image is not None
            try:
                path = await asyncio.to_thread(
                    storage.save_photo, self.photos_dir, image, taken_at, short_name, ext or ".bin"
                )
                photo_rel = path.relative_to(self.data_dir).as_posix()
            except Exception:
                logger.exception("存照片失敗")
                outcome = failure_outcome("內部錯誤")

        record = Record(
            taken_at=taken_at,
            uploader=uploader,
            image_type=outcome.image_type,
            title=outcome.title,
            artist=outcome.artist,
            genre=" / ".join(outcome.genre) if outcome.genre else NONE_WORD,
            lyrics=outcome.lyrics,
            photo_rel=photo_rel,
            confidence=outcome.confidence,
            album_title=outcome.album_title,
            release_type=outcome.release_type,
        )
        # ExcelStore 內部可能因為檔案被鎖住而 sleep 重試，放到執行緒裡跑才不會卡住整個程式
        daily_count = await asyncio.to_thread(self.store.append, record)
        return ProcessResult(outcome, daily_count)
