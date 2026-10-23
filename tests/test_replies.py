"""replies.py：回覆文字與結果符號（architecture.md 第 10 節）。"""

import pytest

from music_bot.models import Outcome
from music_bot.replies import compose_rejection, compose_reply, result_symbol


def music(**kw) -> Outcome:
    base = dict(
        image_type="專輯封面",
        title="ドライフラワー",
        artist="優里",
        genre=["J-Pop", "抒情"],
        lyrics="",
        confidence="高",
    )
    base.update(kw)
    return Outcome(**base)


def non_music(**kw) -> Outcome:
    base = dict(
        image_type="非音樂相關",
        title="手搖飲料",
        artist="無",
        genre=[],
        lyrics="一杯珍珠奶茶",
        confidence="無",
    )
    base.update(kw)
    return Outcome(**base)


def failure(**kw) -> Outcome:
    base = dict(
        image_type="處理失敗",
        title="無法判斷圖片內容",
        artist="無",
        genre=[],
        lyrics="逾時",
        confidence="無",
        failure_reason="逾時",
    )
    base.update(kw)
    return Outcome(**base)


# ---------- 結果符號 ----------


@pytest.mark.parametrize(
    "image_type, symbol",
    [
        ("專輯封面", "✅"),
        ("歌詞手稿", "✅"),
        ("其他音樂相關", "✅"),
        ("非音樂相關", "🚫"),
        ("處理失敗", "❌"),
    ],
)
def test_result_symbol(image_type, symbol):
    assert result_symbol(Outcome(image_type, "t", "a", [], "", "高")) == symbol


# ---------- 音樂相關 ----------


def test_music_reply_has_all_fields_and_ends_with_daily_count():
    text = compose_reply(music(), 7)
    assert text.startswith("✅")
    for expected in ("專輯封面", "ドライフラワー", "優里", "J-Pop / 抒情", "信心：高"):
        assert expected in text
    assert text.splitlines()[-1] == "📊 今天已紀錄音樂數：7"


@pytest.mark.parametrize("kind", ["專輯封面", "歌詞手稿", "其他音樂相關"])
def test_each_music_type_is_named_in_reply(kind):
    assert kind in compose_reply(music(image_type=kind), 1)


def test_medium_and_low_confidence_get_a_caution():
    assert "僅供參考" in compose_reply(music(confidence="中"), 1)
    assert "僅供參考" in compose_reply(music(confidence="低"), 1)
    assert "僅供參考" not in compose_reply(music(confidence="高"), 1)


def test_empty_genre_is_shown_as_none_word():
    assert "曲風：無" in compose_reply(music(genre=[]), 1)


def test_reply_never_shows_lyrics_even_if_the_outcome_carries_text():
    """歌詞轉錄功能已停用：回覆裡沒有歌詞區塊，結果物件帶了文字也不顯示。"""
    text = compose_reply(music(lyrics="這一行不該出現在回覆裡\n這一行也不該"), 1)
    assert "歌詞/文字：" not in text
    assert "不該" not in text
    assert "> " not in text


# ---------- 非音樂 ----------


def test_non_music_reply_says_what_it_is_and_still_shows_count():
    text = compose_reply(non_music(), 3)
    assert text.startswith("🚫")
    assert "你傳的不是音樂封面或歌詞，是「手搖飲料」" in text
    assert text.splitlines()[-1] == "📊 今天已紀錄音樂數：3"


# ---------- 失敗 ----------


def test_failure_reply_asks_to_resend_and_shows_reason_and_count():
    text = compose_reply(failure(), 4)
    assert text.startswith("❌")
    assert "分析失敗，請再傳一次" in text
    assert "逾時" in text
    assert "無法判斷圖片內容" in text
    assert text.splitlines()[-1] == "📊 今天已紀錄音樂數：4"


def test_failure_reply_shows_content_when_we_have_it():
    text = compose_reply(failure(title="一張風景照"), 4)
    assert "一張風景照" in text


# ---------- 排隊太多 ----------


def test_rejection_message():
    text = compose_rejection()
    assert text.startswith("❌")
    assert "排隊" in text and "請稍後再傳" in text


# ---------- 安全與長度 ----------


def test_markdown_in_model_text_is_escaped():
    text = compose_reply(music(title="*粗體* [連結](http://x) `code`"), 1)
    assert "\\*粗體\\*" in text
    assert "\\`code\\`" in text


def test_reply_never_exceeds_discord_limit():
    huge = music(title="t" * 200, artist="a" * 200, lyrics="\n".join("L" * 500 for _ in range(50)))
    assert len(compose_reply(huge, 99)) <= 1800
    assert len(compose_reply(failure(title="x" * 5000), 99)) <= 1800
    assert len(compose_reply(non_music(title="y" * 5000), 99)) <= 1800


def test_daily_count_line_survives_truncation():
    huge = music(lyrics="\n".join("L" * 500 for _ in range(50)))
    assert compose_reply(huge, 12).splitlines()[-1] == "📊 今天已紀錄音樂數：12"


# ---------- 找歌詞與聽歌連結（機器人本身不輸出歌詞，只給搜尋連結） ----------

import re  # noqa: E402
from urllib.parse import parse_qs, unquote, urlparse  # noqa: E402

LINK_RE = re.compile(r"\[([^\]]+)\]\(<([^>]+)>\)")
APP_NAMES = {"Spotify", "YouTube Music", "QQ音樂", "網易雲音樂"}


def lyrics_line(text: str) -> str | None:
    return next((ln for ln in text.splitlines() if ln.startswith("🔎")), None)


def apps_line(text: str) -> str | None:
    return next((ln for ln in text.splitlines() if ln.startswith("🎧")), None)


def apps(text: str) -> dict[str, str]:
    line = apps_line(text)
    return dict(LINK_RE.findall(line)) if line else {}


def lyrics_url(text: str) -> str:
    line = lyrics_line(text)
    return line.split("<", 1)[1].rsplit(">", 1)[0]


def lyrics_query(text: str) -> str:
    return parse_qs(urlparse(lyrics_url(text)).query)["q"][0]


def app_queries(text: str) -> dict[str, str]:
    """把每個 App 連結裡的搜尋字詞解出來。"""
    found = apps(text)
    out = {}
    if "Spotify" in found:
        out["Spotify"] = unquote(found["Spotify"].rsplit("/", 1)[1])
    if "YouTube Music" in found:
        out["YouTube Music"] = parse_qs(urlparse(found["YouTube Music"]).query)["q"][0]
    if "QQ音樂" in found:
        out["QQ音樂"] = parse_qs(urlparse(found["QQ音樂"]).query)["w"][0]
    if "網易雲音樂" in found:
        fragment = urlparse(found["網易雲音樂"]).fragment
        out["網易雲音樂"] = parse_qs(fragment.split("?", 1)[1])["s"][0]
    return out


def all_urls(text: str) -> list[str]:
    urls = list(apps(text).values())
    if lyrics_line(text):
        urls.append(lyrics_url(text))
    return urls


# --- 找歌詞（Google） ---


def test_music_reply_has_a_lyrics_search_link_without_embed():
    line = lyrics_line(compose_reply(music(), 1))
    assert line is not None
    assert line.startswith("🔎 找歌詞：<https://www.google.com/search?q=")
    assert line.endswith(">")  # 角括號：Discord 不會展開成預覽卡片


def test_lyrics_link_searches_for_title_artist_and_the_word_lyrics():
    q = lyrics_query(compose_reply(music(title="某專輯", artist="某歌手"), 1))
    assert q == "某專輯 某歌手 歌詞"


# --- 聽歌連結（各音樂 App） ---


def test_music_reply_has_a_link_for_each_music_app():
    text = compose_reply(music(), 1)
    assert apps_line(text).startswith("🎧")
    assert set(apps(text)) == APP_NAMES


def test_each_app_link_points_to_that_apps_search_page():
    found = apps(compose_reply(music(), 1))
    assert found["Spotify"].startswith("https://open.spotify.com/search/")
    assert found["YouTube Music"].startswith("https://music.youtube.com/search?q=")
    assert found["QQ音樂"].startswith("https://y.qq.com/n/ryqq/search?w=")
    assert found["網易雲音樂"].startswith("https://music.163.com/#/search/m/?s=")


def test_app_searches_use_title_and_artist_but_not_the_word_lyrics():
    queries = app_queries(compose_reply(music(title="某專輯", artist="某歌手"), 1))
    assert set(queries) == APP_NAMES
    assert all(q == "某專輯 某歌手" for q in queries.values()), queries


def test_app_links_are_masked_so_discord_shows_no_preview_cards():
    line = apps_line(compose_reply(music(), 1))
    assert line.count("](<") == 4 and line.count(">)") == 4


# --- 什麼時候附、什麼時候不附 ---


def test_links_are_added_for_every_music_type():
    for kind in ("專輯封面", "歌詞手稿", "其他音樂相關"):
        text = compose_reply(music(image_type=kind), 1)
        assert lyrics_line(text) is not None and apps_line(text) is not None


def test_no_links_for_non_music_or_failure():
    for outcome in (non_music(), failure()):
        text = compose_reply(outcome, 1)
        assert lyrics_line(text) is None and apps_line(text) is None


@pytest.mark.parametrize("title", ["無法辨識", "不明", "無", "", "   "])
def test_no_links_when_the_title_is_unusable(title):
    text = compose_reply(music(title=title), 1)
    assert lyrics_line(text) is None and apps_line(text) is None


def test_missing_artist_is_left_out_of_every_query():
    text = compose_reply(music(title="某專輯", artist="無"), 1)
    assert lyrics_query(text) == "某專輯 歌詞"
    assert all(q == "某專輯" for q in app_queries(text).values())


# --- 安全 ---


def test_links_are_safe_with_hostile_text_in_title_and_artist():
    nasty = music(title='@everyone "x" <b>(a) [b](http://evil) > y', artist="@here & rm -rf")
    text = compose_reply(nasty, 1)
    urls = all_urls(text)
    assert len(urls) == 5
    for url in urls:
        for bad in (" ", '"', "<", ">", "(", ")", "@", "[", "]", "\n"):
            assert bad not in url, (bad, url)


def test_each_link_stays_short_because_names_are_capped():
    text = compose_reply(music(title="長" * 200, artist="歌" * 200), 1)
    for url in all_urls(text):
        assert len(url) <= 450, len(url)


# --- 排版與長度 ---


def test_links_come_after_the_confidence_line_and_count_stays_last():
    lines = compose_reply(music(), 5).splitlines()
    confidence = next(i for i, ln in enumerate(lines) if ln.startswith("信心："))
    google = next(i for i, ln in enumerate(lines) if ln.startswith("🔎"))
    apps_i = next(i for i, ln in enumerate(lines) if ln.startswith("🎧"))
    assert confidence < google < apps_i < len(lines) - 1
    assert lines[-1] == "📊 今天已紀錄音樂數：5"


def test_reply_with_all_links_fits_discord_limit_and_never_cuts_a_link():
    huge = music(
        title="長" * 200,
        artist="歌" * 200,
        lyrics="\n".join("L" * 500 for _ in range(50)),
    )
    text = compose_reply(huge, 99)
    assert len(text) <= 1800
    assert text.splitlines()[-1] == "📊 今天已紀錄音樂數：99"
    line = apps_line(text)
    if line:  # 出現的每一條都是完整的 [名稱](<網址>)
        assert " · ".join(f"[{n}](<{u}>)" for n, u in LINK_RE.findall(line)) == line.split("：", 1)[1]
    google = lyrics_line(text)
    if google:
        assert google.count("<") == 1 and google.endswith(">")


def test_apps_line_drops_trailing_apps_when_space_is_short_but_never_cuts_one():
    crowded = music(title="長" * 200, artist="歌" * 200)  # 內文很長，連結放不下全部
    text = compose_reply(crowded, 7)
    assert len(text) <= 1800
    found = apps(text)
    assert len(found) < 4
    assert list(found) == ["Spotify", "YouTube Music", "QQ音樂", "網易雲音樂"][: len(found)]  # 從尾巴開始捨棄
    line = apps_line(text)
    assert " · ".join(f"[{n}](<{u}>)" for n, u in LINK_RE.findall(line)) == line.split("：", 1)[1]


def test_reply_with_links_still_fits_discord_limit():
    huge = music(title="t" * 200, artist="a" * 200, lyrics="\n".join("L" * 500 for _ in range(50)))
    assert len(compose_reply(huge, 99)) <= 1800


# ---------- 歌曲名稱、專輯名稱、發行類型 ----------


def line_starting(text: str, prefix: str) -> str | None:
    return next((ln for ln in text.splitlines() if ln.startswith(prefix)), None)


def test_reply_shows_song_album_and_release_type_on_separate_lines():
    text = compose_reply(music(title="小深情", album_title="嗨！人間 Hi! world", release_type="EP"), 1)
    assert line_starting(text, "歌曲名稱：") == "歌曲名稱：小深情"
    assert line_starting(text, "專輯名稱：") == "專輯名稱：嗨！人間 Hi! world"
    assert line_starting(text, "發行類型：") == "發行類型：EP"


def test_unknown_album_and_release_type_are_written_as_unknown():
    text = compose_reply(music(album_title="不明", release_type="不明"), 1)
    assert line_starting(text, "專輯名稱：") == "專輯名稱：不明"
    assert line_starting(text, "發行類型：") == "發行類型：不明"


def test_unknown_song_is_written_as_unknown():
    assert line_starting(compose_reply(music(title="不明"), 1), "歌曲名稱：") == "歌曲名稱：不明"


def test_album_text_from_the_model_cannot_become_discord_formatting():
    text = compose_reply(music(album_title="*粗體* [連結](http://x)"), 1)
    assert r"\*粗體\*" in text


def test_song_album_and_release_lines_come_before_the_artist_line():
    lines = compose_reply(music(), 1).splitlines()
    idx = {k: next(i for i, ln in enumerate(lines) if ln.startswith(k)) for k in ("歌曲名稱：", "專輯名稱：", "發行類型：", "歌手/樂團：")}
    assert idx["歌曲名稱："] < idx["專輯名稱："] < idx["發行類型："] < idx["歌手/樂團："]


def test_links_search_the_song_when_it_is_known():
    text = compose_reply(music(title="某首歌", album_title="某專輯", artist="某歌手"), 1)
    assert all(q == "某首歌 某歌手" for q in app_queries(text).values())
    assert lyrics_query(text) == "某首歌 某歌手 歌詞"


def test_links_search_the_album_when_the_song_is_unknown():
    text = compose_reply(music(title="不明", album_title="某專輯", artist="某歌手"), 1)
    assert set(apps(text)) == APP_NAMES
    assert all(q == "某專輯 某歌手" for q in app_queries(text).values())


def test_lyrics_link_needs_a_song_so_it_is_left_out_when_only_the_album_is_known():
    text = compose_reply(music(title="不明", album_title="某專輯"), 1)
    assert lyrics_line(text) is None  # 搜專輯的「歌詞」沒有意義
    assert apps_line(text) is not None


def test_no_links_when_both_song_and_album_are_unknown():
    text = compose_reply(music(title="不明", album_title="不明"), 1)
    assert lyrics_line(text) is None and apps_line(text) is None


def test_non_music_and_failure_replies_do_not_show_album_lines():
    for outcome in (non_music(), failure()):
        text = compose_reply(outcome, 1)
        assert "專輯名稱：" not in text and "發行類型：" not in text


# ---------- 圖上有兩個名稱、分不出哪個是歌曲時 ----------

TWO_NAMES = "不明（圖上另有：嗨！人間）"


def test_two_names_note_is_shown_in_the_song_line():
    text = compose_reply(music(title=TWO_NAMES, album_title="小深情"), 1)
    assert line_starting(text, "歌曲名稱：") == f"歌曲名稱：{TWO_NAMES}"
    assert line_starting(text, "專輯名稱：") == "專輯名稱：小深情"


def test_links_search_the_album_when_the_song_line_is_a_two_names_note():
    text = compose_reply(music(title=TWO_NAMES, album_title="小深情", artist="周深"), 1)
    assert all(q == "小深情 周深" for q in app_queries(text).values())
    assert lyrics_line(text) is None  # 歌名不明，不附找歌詞連結


def test_no_links_when_the_song_is_a_note_and_the_album_is_unknown_too():
    text = compose_reply(music(title=TWO_NAMES, album_title="不明"), 1)
    assert lyrics_line(text) is None and apps_line(text) is None


# ---------- 歌名不明的歌詞截圖：用歌詞搜尋的連結 ----------

SNIPPET = "測試用的關鍵句子"
SNIPPET_LINK_RE = re.compile(r"^🔎 用歌詞搜尋：\[([^\]]+)\]\(<([^>]+)>\)$")


def snippet_line(text: str) -> str | None:
    return next((ln for ln in text.splitlines() if ln.startswith("🔎 用歌詞搜尋")), None)


def snippet_url(text: str) -> str:
    return SNIPPET_LINK_RE.match(snippet_line(text)).group(2)


def lyric_photo(**kw) -> Outcome:
    base = dict(image_type="歌詞手稿", title="不明", album_title="不明", artist="無", lyric_snippet=SNIPPET, confidence="低")
    base.update(kw)
    return music(**base)


def test_unknown_song_with_a_snippet_gets_a_lyrics_search_link():
    line = snippet_line(compose_reply(lyric_photo(), 1))
    assert line is not None
    match = SNIPPET_LINK_RE.match(line)
    assert match is not None, line
    assert match.group(2).startswith("https://www.google.com/search?q=")


def test_the_snippet_is_searched_as_an_exact_phrase_with_the_word_lyrics():
    url = snippet_url(compose_reply(lyric_photo(), 1))
    assert parse_qs(urlparse(url).query)["q"][0] == f'"{SNIPPET}" 歌詞'


def test_the_snippet_never_appears_in_the_reply_text():
    """關鍵句只放在連結的網址裡（而且是編碼過的），回覆文字裡看不到。"""
    text = compose_reply(lyric_photo(), 1)
    assert SNIPPET not in text
    assert SNIPPET not in "".join(ln for ln in text.splitlines() if not ln.startswith("🔎 用歌詞搜尋"))


def test_snippet_link_is_left_out_when_the_song_is_known():
    assert snippet_line(compose_reply(lyric_photo(title="某首歌"), 1)) is None


def test_snippet_link_is_left_out_when_there_is_no_snippet():
    assert snippet_line(compose_reply(lyric_photo(lyric_snippet=""), 1)) is None
    assert snippet_line(compose_reply(lyric_photo(lyric_snippet="   "), 1)) is None


def test_snippet_link_is_left_out_for_non_music_and_failure():
    assert snippet_line(compose_reply(non_music(lyric_snippet=SNIPPET), 1)) is None
    assert snippet_line(compose_reply(failure(lyric_snippet=SNIPPET), 1)) is None


def test_hostile_snippet_cannot_break_out_of_the_link():
    nasty = '")](<http://evil>) @everyone `x` <b>'
    text = compose_reply(lyric_photo(lyric_snippet=nasty), 1)
    line = snippet_line(text)
    match = SNIPPET_LINK_RE.match(line)
    assert match is not None, line
    url = match.group(2)
    for bad in (" ", '"', "<", ">", "(", ")", "@", "[", "]", "`"):
        assert bad not in url, (bad, url)
    assert line.count("](<") == 1 and line.count(">)") == 1
    assert "@everyone" not in text


def test_double_quotes_inside_the_snippet_do_not_break_the_exact_phrase():
    url = snippet_url(compose_reply(lyric_photo(lyric_snippet='前面"中間"後面'), 1))
    assert parse_qs(urlparse(url).query)["q"][0] == '"前面中間後面" 歌詞'


def test_snippet_link_comes_before_the_app_links_and_count_stays_last():
    lines = compose_reply(lyric_photo(album_title="某專輯"), 4).splitlines()
    snippet_i = next(i for i, ln in enumerate(lines) if ln.startswith("🔎 用歌詞搜尋"))
    apps_i = next(i for i, ln in enumerate(lines) if ln.startswith("🎧"))
    assert snippet_i < apps_i < len(lines) - 1
    assert lines[-1] == "📊 今天已紀錄音樂數：4"


def test_snippet_link_fits_the_limit_and_is_never_cut():
    crowded = lyric_photo(lyric_snippet="字" * 40, artist="歌" * 200, album_title="專" * 200)
    text = compose_reply(crowded, 9)
    assert len(text) <= 1800
    assert text.splitlines()[-1] == "📊 今天已紀錄音樂數：9"
    line = snippet_line(text)
    if line:
        assert SNIPPET_LINK_RE.match(line)
