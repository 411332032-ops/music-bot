"""處理一張圖片之後的結果（成功、非音樂、失敗都用同一個形狀）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import FAILURE_TYPE, MUSIC_TYPES, NON_MUSIC_TYPE


@dataclass(frozen=True)
class Outcome:
    image_type: str  # 專輯封面／歌詞手稿／其他音樂相關／非音樂相關／處理失敗
    title: str  # 非音樂＝這張圖是什麼；失敗＝有拿到的內容，沒有就是固定文字
    artist: str
    genre: list[str] = field(default_factory=list)
    lyrics: str = ""  # 非音樂＝較完整的內容說明；失敗＝失敗原因類別
    confidence: str = "無"  # 高／中／低；非音樂與失敗是「無」
    failure_reason: str | None = None
    # 專輯、EP 或單曲的名稱，以及它是哪一種。認不出是「不明」；非音樂與失敗是「無」
    album_title: str = "不明"
    release_type: str = "不明"
    # 歌名不明的歌詞截圖才有：圖上挑的一小句，只給回覆產生搜尋連結。不存進 Excel、不顯示在回覆文字裡
    lyric_snippet: str = ""

    @property
    def is_music(self) -> bool:
        return self.image_type in MUSIC_TYPES

    @property
    def is_non_music(self) -> bool:
        return self.image_type == NON_MUSIC_TYPE

    @property
    def is_failure(self) -> bool:
        return self.image_type == FAILURE_TYPE
