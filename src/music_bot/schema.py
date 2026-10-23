"""claude -p 的輸出約定：解析、驗證、自動修正（architecture.md 第 5 節）。

原則：agent 不信任 claude 回來的任何東西。小問題自動修，大問題丟 SchemaError，
由呼叫端決定要重試還是降級成「處理失敗」。
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    model_validator,
)

ImageType = Literal["專輯封面", "歌詞手稿", "其他音樂相關", "非音樂相關"]
Confidence = Literal["高", "中", "低"]
# 發行類型：這首歌（或這張封面）屬於專輯、EP 還是單曲。認不出就是「不明」；非音樂是「無」。
ReleaseType = Literal["專輯", "EP", "單曲", "不明", "無"]

# 真正會送去分析、存檔的圖片格式（detect_image_ext 認得的那四種）
IMAGE_EXTS = (".jpg", ".png", ".gif", ".webp")

MUSIC_TYPES = ("專輯封面", "歌詞手稿", "其他音樂相關")
LYRICS_TYPE = "歌詞手稿"
NON_MUSIC_TYPE = "非音樂相關"
FAILURE_TYPE = "處理失敗"  # 不是 claude 回的值，是 agent 降級時自己用的

UNKNOWN = "不明"
NONE_WORD = "無"

MAX_TITLE = 200
MAX_ALBUM = 200
MAX_ARTIST = 200
MAX_GENRE_LABEL = 40
MAX_GENRES = 3
MAX_LYRICS = 20000
MAX_SNIPPET = 40  # 歌名不明的歌詞截圖，拿來搜尋的那一小句

GenreLabel = Annotated[StrictStr, Field(max_length=MAX_GENRE_LABEL)]

_ARTIST_PLACEHOLDER = NONE_WORD
# 模型偶爾會用英文回發行類型，翻成我們的固定寫法
_RELEASE_TYPE_ALIASES = {"album": "專輯", "ep": "EP", "single": "單曲"}


_UNKNOWN_NAMES = ("", UNKNOWN, NONE_WORD, "無法辨識")  # 「無法辨識」是舊版的寫法
# 圖上有兩個名稱、分不出哪個是歌曲時，歌曲名稱欄寫成「不明（圖上另有：另一個名稱）」。
# 只認這個固定的開頭，才不會把「不明白」這種真的歌名誤判成不明。
_TWO_NAMES_NOTE_PREFIX = "不明（圖上另有："


def is_unknown_name(name: str) -> bool:
    """這個名稱是不是「沒有可用的名稱」：拿去當檔名簡稱、搜尋關鍵字都沒有意義。"""
    name = name.strip()
    return name in _UNKNOWN_NAMES or name.startswith(_TWO_NAMES_NOTE_PREFIX)


class SchemaError(Exception):
    """claude 的輸出不合格。

    summary：agent 自己寫的錯誤摘要（可安全地拿去給模型重試時看），
             絕不包含模型的原始輸出。
    salvaged_title：如果 title 是可用的文字，保留下來當降級時的「圖片內容」。
    """

    def __init__(self, summary: str, salvaged_title: str | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.salvaged_title = salvaged_title


class Analysis(BaseModel):
    is_music_related: StrictBool
    type: ImageType
    title: StrictStr = Field(max_length=MAX_TITLE)  # 歌曲名稱；非音樂＝這張圖是什麼
    album_title: StrictStr = Field(default=UNKNOWN, max_length=MAX_ALBUM)  # 專輯、EP 或單曲的名稱
    release_type: ReleaseType = UNKNOWN
    artist: StrictStr = Field(max_length=MAX_ARTIST)
    genre_estimate: list[GenreLabel] = Field(max_length=MAX_GENRES)
    lyrics_text: StrictStr = Field(max_length=MAX_LYRICS)
    # 只有「歌名不明的歌詞截圖」才會有：圖上挑一小句，只拿來產生搜尋連結，不存檔、不顯示
    lyric_snippet: StrictStr = Field(default="", max_length=MAX_SNIPPET)
    confidence: Confidence = "低"

    @model_validator(mode="after")
    def _type_matches_flag(self) -> "Analysis":
        if (self.type == NON_MUSIC_TYPE) == self.is_music_related:
            raise ValueError("inconsistent: type and is_music_related disagree")
        return self


def json_schema() -> dict[str, Any]:
    """給 `claude --json-schema` 的規格；與驗證用同一份定義。"""
    schema = Analysis.model_json_schema()
    schema.pop("title", None)
    for prop in schema["properties"].values():
        prop.pop("title", None)
    schema["required"] = list(schema["properties"])  # 要求模型每個欄位都給
    schema["additionalProperties"] = False
    return schema


# ---------- 解析 ----------


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _parse_inner_text(text: str) -> dict[str, Any]:
    text = _strip_fence(text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise SchemaError("內容裡找不到 JSON 物件")
    try:
        inner = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        raise SchemaError("內容不是合法的 JSON") from None
    if not isinstance(inner, dict):
        raise SchemaError("內容不是 JSON 物件")
    return inner


def extract_payload(stdout: str) -> dict[str, Any]:
    """從 `claude -p --output-format json` 的整段輸出取出分析結果（尚未驗證欄位）。"""
    try:
        outer = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        raise SchemaError("claude 的輸出不是合法的 JSON") from None
    if not isinstance(outer, dict):
        raise SchemaError("claude 的輸出不是 JSON 物件")
    if outer.get("is_error"):
        raise SchemaError("claude 回報執行錯誤")

    structured = outer.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = outer.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        return _parse_inner_text(result)
    raise SchemaError("輸出裡沒有結果欄位")


# ---------- 驗證與自動修正 ----------


def _clean_text(value: Any, limit: int, placeholder: str | None = None) -> Any:
    if not isinstance(value, str):
        return value  # 型別錯誤留給驗證去擋
    value = value.strip()[:limit]
    if value == "" and placeholder is not None:
        return placeholder
    return value


def _clean_genres(value: Any) -> Any:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return value
    labels = [_clean_text(v, MAX_GENRE_LABEL) for v in value]
    labels = [v for v in labels if v != "" and v is not None]
    return labels[:MAX_GENRES]


def _clean_snippet(value: Any) -> str:
    """搜尋用的一小句：換行、tab、控制字元、全形空白都併成一個空白，再截短。非文字就當作沒有。"""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f\s]+", " ", value).strip()[:MAX_SNIPPET]


def _normalize_release_type(value: Any) -> str:
    if isinstance(value, str):
        value = value.strip()
        if value in ("專輯", "EP", "單曲", UNKNOWN, NONE_WORD):
            return value
        return _RELEASE_TYPE_ALIASES.get(value.lower(), UNKNOWN)
    return UNKNOWN


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """小問題在這裡修掉；必填欄位缺了不補（讓驗證回報），只丟掉規格以外的欄位。"""
    known = set(Analysis.model_fields)
    data = {k: v for k, v in raw.items() if k in known}
    if "title" in data:
        data["title"] = _clean_text(data["title"], MAX_TITLE, UNKNOWN)
    # 專輯名稱與發行類型是後來新增的欄位，模型很容易漏；漏了就是「不明」，不值得為此重試
    data["album_title"] = _clean_text(data.get("album_title", UNKNOWN), MAX_ALBUM, UNKNOWN)
    data["release_type"] = _normalize_release_type(data.get("release_type"))
    data["lyric_snippet"] = _clean_snippet(data.get("lyric_snippet"))
    if "artist" in data:
        data["artist"] = _clean_text(data["artist"], MAX_ARTIST, _ARTIST_PLACEHOLDER)
    if "lyrics_text" in data:
        data["lyrics_text"] = _clean_text(data["lyrics_text"], MAX_LYRICS)
    if "genre_estimate" in data:
        data["genre_estimate"] = _clean_genres(data["genre_estimate"])
    if data.get("confidence") not in ("高", "中", "低"):
        data["confidence"] = "低"
    return data


def _summarize(error: ValidationError) -> str:
    """只用欄位名稱與錯誤類別組摘要，不回放模型給的值。"""
    parts: list[str] = []
    for err in error.errors():
        field = ".".join(str(p) for p in err["loc"] if not isinstance(p, int))
        if err["type"] == "missing":
            parts.append(f"缺少必填欄位 {field}")
        elif not field:
            parts.append("type 與 is_music_related 互相矛盾")
        else:
            parts.append(f"欄位 {field} 的內容或型別不合格")
    return "；".join(dict.fromkeys(parts)) or "欄位驗證失敗"


def validate_analysis(payload: Any) -> Analysis:
    if not isinstance(payload, dict):
        raise SchemaError("結果不是 JSON 物件")
    data = _normalize(payload)
    try:
        return Analysis.model_validate(data)
    except ValidationError as e:
        title = data.get("title")
        salvaged = title if isinstance(title, str) and title else None
        raise SchemaError(_summarize(e), salvaged_title=salvaged) from None
