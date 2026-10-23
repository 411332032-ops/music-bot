"""組合回覆上傳者的文字（architecture.md 第 10 節）。

每一則處理結果的回覆都一定附上「當日已紀錄音樂數」。
"""

from __future__ import annotations

from urllib.parse import quote, quote_plus

from discord.utils import escape_markdown

from .models import Outcome
from .schema import is_unknown_name

MAX_REPLY_CHARS = 1800  # Discord 上限是 2000，留一點空間

# 搜尋連結：機器人自己不輸出歌詞（模型憑記憶背的歌詞不可靠，也有著作權問題），
# 只給「找歌詞」和「在各音樂 App 搜尋這首歌」的連結。
# 要換成別的網站，改下面的網址（{q} 的位置會填入編碼過的搜尋字詞）。
LYRICS_SEARCH_URL = "https://www.google.com/search?q="
# (顯示名稱, 網址範本, 空白要編成 %20 還是 +)；順序就是回覆裡的順序，放不下時從最後面開始捨棄
_MUSIC_APPS: tuple[tuple[str, str, bool], ...] = (
    ("Spotify", "https://open.spotify.com/search/{q}", True),
    ("YouTube Music", "https://music.youtube.com/search?q={q}", False),
    ("QQ音樂", "https://y.qq.com/n/ryqq/search?w={q}&t=song", False),
    ("網易雲音樂", "https://music.163.com/#/search/m/?s={q}&type=1", False),
)
LINK_TITLE_CHARS = 25  # 中文字編碼後每個字約 9 個字元，所以要限制長度，連結才不會爆長
LINK_ARTIST_CHARS = 12
_NO_ARTIST = "無"


def result_symbol(outcome: Outcome) -> str:
    """原訊息上用來取代 ⏳ 的結果符號。"""
    if outcome.is_failure:
        return "❌"
    if outcome.is_non_music:
        return "🚫"
    return "✅"


def _esc(text: str) -> str:
    # 名稱、歌詞都是 AI 看別人的圖得到的文字，不能讓它在 Discord 裡變成粗體、連結等格式
    return escape_markdown(text)


def _count_line(daily_count: int) -> str:
    return f"📊 今天已紀錄音樂數：{daily_count}"


def _assemble(body_lines: list[str], daily_count: int) -> str:
    """內文太長就截短內文；最後一行的當日數永遠保留。"""
    tail = _count_line(daily_count)
    head = "\n".join(body_lines)
    room = MAX_REPLY_CHARS - len(tail) - 1
    if len(head) > room:
        head = head[: room - 1] + "…"
    return head + "\n" + tail


def _confidence_text(confidence: str) -> str:
    if confidence == "中":
        return "信心：中（僅供參考）"
    if confidence == "低":
        return "信心：低（可能不準，僅供參考）"
    return f"信心：{confidence}"


def _search_words(title: str, artist: str) -> list[str] | None:
    """搜尋用的字：歌名加歌手（歌手沒有就省略）。名稱用不上時回傳 None。"""
    if is_unknown_name(title):
        return None
    title = title.strip()[:LINK_TITLE_CHARS]
    artist = artist.strip()[:LINK_ARTIST_CHARS]
    return [title, *([artist] if artist and artist != _NO_ARTIST else [])]


# 名稱來自模型看別人的圖，所以所有連結的搜尋字詞都經過網址編碼：
# 空白、引號、括號、@ 等都會變成 %XX，不可能在 Discord 裡變成格式或提及別人。
def lyrics_search_link(title: str, artist: str) -> str | None:
    words = _search_words(title, artist)
    if words is None:
        return None
    return LYRICS_SEARCH_URL + quote_plus(" ".join([*words, "歌詞"]))


def music_app_links(title: str, artist: str) -> list[tuple[str, str]]:
    """各音樂 App 的「搜尋這首歌」連結，回傳 [(App 名稱, 網址), ...]。"""
    words = _search_words(title, artist)
    if words is None:
        return []
    query = " ".join(words)
    links = []
    for name, template, space_as_percent20 in _MUSIC_APPS:
        encoded = quote(query, safe="") if space_as_percent20 else quote_plus(query)
        links.append((name, template.format(q=encoded)))
    return links


def _lyrics_link_line(outcome: Outcome) -> str | None:
    url = lyrics_search_link(outcome.title, outcome.artist)
    # 用角括號包起來：Discord 仍然可以點，但不會展開成大張的預覽卡片
    return f"🔎 找歌詞：<{url}>" if url else None


def _snippet_link_line(outcome: Outcome) -> str | None:
    """歌名不明的歌詞截圖：用圖上那一小句歌詞去搜尋（Google 比對歌詞的效果遠好過任何 API）。

    連結的文字是固定的，那一句歌詞只出現在（編碼過的）網址裡，回覆文字裡看不到；
    會用雙引號把它包起來當成「完整詞組」搜尋。這句不存進 Excel。
    """
    snippet = outcome.lyric_snippet.replace('"', "").strip()
    if not snippet or not is_unknown_name(outcome.title):
        return None
    url = LYRICS_SEARCH_URL + quote_plus(f'"{snippet}" 歌詞')
    return f"🔎 用歌詞搜尋：[點這裡](<{url}>)"


def _apps_link_line(outcome: Outcome, room: int) -> str | None:
    """「🎧 聽這首：」那一行。放不下全部時從最後面的 App 開始捨棄，但每一條都一定是完整的。

    有歌名就搜歌名；只認得專輯（例如封面上沒標歌名）就搜專輯名稱。
    """
    name_to_search = outcome.album_title if is_unknown_name(outcome.title) else outcome.title
    links = [f"[{name}](<{url}>)" for name, url in music_app_links(name_to_search, outcome.artist)]
    prefix = "🎧 聽這首："
    for count in range(len(links), 0, -1):
        line = prefix + " · ".join(links[:count])
        if len(line) + 1 <= room:  # +1 是換行
            return line
    return None


def compose_reply(outcome: Outcome, daily_count: int) -> str:
    if outcome.is_failure:
        reason = outcome.failure_reason or "未知"
        return _assemble(
            [
                f"❌ 分析失敗，請再傳一次（原因：{_esc(reason)}）",
                f"我看到的內容：{_esc(outcome.title)}",
            ],
            daily_count,
        )

    if outcome.is_non_music:
        return _assemble(
            [f"🚫 你傳的不是音樂封面或歌詞，是「{_esc(outcome.title)}」。"],
            daily_count,
        )

    genre = " / ".join(outcome.genre) if outcome.genre else "無"
    body = [
        "✅ 辨識完成",
        f"類型：{outcome.image_type}",
        f"歌曲名稱：{_esc(outcome.title)}",
        f"專輯名稱：{_esc(outcome.album_title)}",
        f"發行類型：{_esc(outcome.release_type)}",
        f"歌手/樂團：{_esc(outcome.artist)}",
        f"曲風：{_esc(genre)}",
        _confidence_text(outcome.confidence),
    ]

    # 連結「整條放得下才放」：空間不夠時寧可少放幾條，也不要把網址截到一半（截一半的網址點了也沒用）
    room = MAX_REPLY_CHARS - len(_count_line(daily_count)) - 1 - len("\n".join(body))
    # 兩種「找歌詞」連結互斥：歌名已知就用歌名搜；歌名不明的歌詞截圖就用那一小句歌詞搜
    google = _lyrics_link_line(outcome) or _snippet_link_line(outcome)
    if google and len(google) + 1 <= room:
        body.append(google)
        room -= len(google) + 1
    apps = _apps_link_line(outcome, room)
    if apps:
        body.append(apps)

    # 歌詞轉錄功能已停用：回覆裡不顯示歌詞（outcome.lyrics 即使有內容也不用）
    return _assemble(body, daily_count)


def compose_rejection() -> str:
    """佇列已滿或這個人排太多張時的回覆（不建檔，所以沒有當日數）。"""
    return "❌ 目前排隊太多（或你同時排的圖太多），請稍後再傳。"
