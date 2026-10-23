"""storage.py 的 Excel 部分：追加、當日數、公式防護（防護 4）、鎖定重試、舊格式轉換（architecture.md 第 8 節）。

欄位（0 起算的 index / Excel 的第幾欄）：
  0/A 日期時間  1/B 上傳者  2/C 圖片類型  3/D 歌曲名稱  4/E 專輯名稱  5/F 發行類型
  6/G 歌手/樂團  7/H 曲風估算  8/I 辨識歌詞/文字內容  9/J 當日已紀錄音樂數  10/K 照片存放位置  11/L 信心程度  12/M 上傳者ID
"""

import json
import logging
from datetime import datetime

import pytest
from openpyxl import Workbook, load_workbook

from music_bot import storage
from music_bot.storage import COLUMNS, LEGACY_COLUMNS_V1, ExcelStore, Record, clean_cell_text

D1 = datetime(2026, 10, 21, 14, 3, 22)
D2 = datetime(2026, 10, 22, 9, 0, 0)


def rec(**kw) -> Record:
    base = dict(
        taken_at=D1,
        uploader="小明",
        image_type="專輯封面",
        title="ドライフラワー",
        artist="優里",
        genre="J-Pop / 抒情",
        lyrics="",
        photo_rel="photos/2026-10-21 14-03-22 ドライフラワー.jpg",
        confidence="中",
        album_title="某專輯",
        release_type="EP",
    )
    base.update(kw)
    return Record(**base)


def store(tmp_path, **kw) -> ExcelStore:
    kw.setdefault("sleep", lambda s: None)
    return ExcelStore(tmp_path / "music_records.xlsx", **kw)


def rows(tmp_path):
    ws = load_workbook(tmp_path / "music_records.xlsx").active
    return [[c.value for c in r] for r in ws.iter_rows(min_row=2)]


# ---------- 建檔與追加 ----------


def test_new_file_gets_header_and_frozen_first_row(tmp_path):
    store(tmp_path).append(rec())
    ws = load_workbook(tmp_path / "music_records.xlsx").active
    assert [c.value for c in ws[1]] == COLUMNS
    assert ws.freeze_panes == "A2"
    assert COLUMNS == [
        "日期時間",
        "上傳者",
        "圖片類型",
        "歌曲名稱",
        "專輯名稱",
        "發行類型",
        "歌手/樂團",
        "曲風估算",
        "辨識歌詞/文字內容",
        "當日已紀錄音樂數",
        "照片存放位置",
        "信心程度",
        "上傳者ID",
    ]


def test_row_values_are_written_in_column_order(tmp_path):
    store(tmp_path).append(rec(lyrics="備註文字\n第二行"))
    (row,) = rows(tmp_path)
    assert row[0] == D1
    assert row[1:9] == [
        "小明",
        "專輯封面",
        "ドライフラワー",
        "某專輯",
        "EP",
        "優里",
        "J-Pop / 抒情",
        "備註文字\n第二行",
    ]
    assert row[9] == 1
    assert row[10] == "photos/2026-10-21 14-03-22 ドライフラワー.jpg"
    assert row[11] == "中"


def test_album_and_release_type_default_to_unknown_when_not_given(tmp_path):
    record = Record(D1, "小明", "專輯封面", "歌", "手", "Pop", "", None, "高")
    assert record.album_title == "不明" and record.release_type == "不明"
    store(tmp_path).append(record)
    (row,) = rows(tmp_path)
    assert row[4] == "不明" and row[5] == "不明"


def test_photo_cell_is_a_relative_hyperlink(tmp_path):
    store(tmp_path).append(rec())
    ws = load_workbook(tmp_path / "music_records.xlsx").active
    cell = ws.cell(row=2, column=11)
    assert cell.hyperlink is not None
    assert cell.hyperlink.target == "photos/2026-10-21 14-03-22 ドライフラワー.jpg"


def test_missing_photo_is_written_as_none_word(tmp_path):
    store(tmp_path).append(rec(photo_rel=None))
    ws = load_workbook(tmp_path / "music_records.xlsx").active
    assert ws.cell(row=2, column=11).value == "無"
    assert ws.cell(row=2, column=11).hyperlink is None


def test_append_adds_below_and_never_overwrites(tmp_path):
    s = store(tmp_path)
    s.append(rec(title="第一張"))
    s.append(rec(title="第二張"))
    assert [r[3] for r in rows(tmp_path)] == ["第一張", "第二張"]


def test_a_new_store_instance_keeps_appending_to_the_same_file(tmp_path):
    """模擬程式重開機。"""
    store(tmp_path).append(rec(title="重開機前"))
    store(tmp_path).append(rec(title="重開機後"))
    assert [r[3] for r in rows(tmp_path)] == ["重開機前", "重開機後"]


# ---------- 當日已紀錄音樂數 ----------


def test_append_returns_the_daily_count(tmp_path):
    s = store(tmp_path)
    assert s.append(rec()) == 1
    assert s.append(rec()) == 2


def test_all_three_music_types_count(tmp_path):
    s = store(tmp_path)
    s.append(rec(image_type="專輯封面"))
    s.append(rec(image_type="歌詞手稿"))
    assert s.append(rec(image_type="其他音樂相關")) == 3


def test_non_music_and_failure_do_not_increase_but_show_current_count(tmp_path):
    s = store(tmp_path)
    s.append(rec())
    assert s.append(rec(image_type="非音樂相關")) == 1
    assert s.append(rec(image_type="處理失敗")) == 1
    assert s.append(rec()) == 2
    assert [r[9] for r in rows(tmp_path)] == [1, 1, 1, 2]


def test_count_resets_on_a_new_calendar_day(tmp_path):
    s = store(tmp_path)
    s.append(rec(taken_at=D1))
    s.append(rec(taken_at=D1))
    assert s.append(rec(taken_at=D2)) == 1


def test_backfilled_older_row_counts_within_its_own_day(tmp_path):
    s = store(tmp_path)
    s.append(rec(taken_at=D1))
    s.append(rec(taken_at=D2))
    assert s.append(rec(taken_at=D1)) == 2  # 補處理昨天的圖，算在昨天


def test_count_follows_the_excel_after_a_manual_row_deletion(tmp_path):
    s = store(tmp_path)
    s.append(rec())
    s.append(rec())
    path = tmp_path / "music_records.xlsx"
    wb = load_workbook(path)
    wb.active.delete_rows(2)
    wb.save(path)
    assert s.append(rec()) == 2


# ---------- 防護 4：公式注入 ----------

INJECTIONS = ["=1+1", "+SUM(1,2)", "-2+3", "@SUM(1,2)", '=HYPERLINK("http://evil","x")', "  =1+1"]
# 會放模型或上傳者文字的欄（Excel 第幾欄）；圖片類型、發行類型是程式自己給的固定值
TEXT_COLUMNS = (2, 4, 5, 7, 8, 9)


@pytest.mark.parametrize("evil", INJECTIONS)
def test_formula_like_text_is_stored_as_plain_text(tmp_path, evil):
    store(tmp_path).append(
        rec(uploader=evil, title=evil, album_title=evil, artist=evil, genre=evil, lyrics=evil)
    )
    ws = load_workbook(tmp_path / "music_records.xlsx").active
    for col in TEXT_COLUMNS:
        cell = ws.cell(row=2, column=col)
        assert cell.data_type != "f", f"欄 {col} 被當成公式"
        assert isinstance(cell.value, str) and cell.value.lstrip().startswith("'")


def test_clean_cell_text_prefixes_dangerous_starts_only():
    assert clean_cell_text("=1+1") == "'=1+1"
    assert clean_cell_text("@x") == "'@x"
    assert clean_cell_text("正常文字") == "正常文字"
    assert clean_cell_text("文字 = 愛") == "文字 = 愛"  # 中間的 = 沒問題
    assert clean_cell_text("") == ""


def test_guard_off_lets_the_formula_through(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "ENABLE_GUARD_4", False)
    store(tmp_path).append(rec(album_title="=1+1"))
    ws = load_workbook(tmp_path / "music_records.xlsx").active
    assert ws.cell(row=2, column=5).data_type == "f"


def test_illegal_control_characters_are_removed_not_crashing(tmp_path):
    store(tmp_path).append(rec(lyrics="a\x00b\x07c\x1fd"))
    (row,) = rows(tmp_path)
    assert row[8] == "abcd"


def test_overlong_cell_is_truncated(tmp_path):
    store(tmp_path).append(rec(lyrics="x" * 40000))
    (row,) = rows(tmp_path)
    assert len(row[8]) <= 32000


# ---------- Excel 被鎖住（Windows 上被使用者打開時） ----------


def lock_replace(monkeypatch, fail_times):
    """讓 os.replace 前 fail_times 次丟 PermissionError，模擬檔案被 Excel 打開。"""
    real = storage.os.replace
    state = {"left": fail_times, "calls": 0}

    def fake(src, dst):
        state["calls"] += 1
        if state["left"] > 0:
            state["left"] -= 1
            raise PermissionError(13, "locked")
        return real(src, dst)

    monkeypatch.setattr(storage.os, "replace", fake)
    return state


def test_locked_file_is_retried_then_succeeds(monkeypatch, tmp_path, caplog):
    sleeps = []
    s = store(tmp_path, sleep=sleeps.append)
    s.append(rec(title="先寫一筆"))  # 先讓檔案存在
    state = lock_replace(monkeypatch, 2)
    with caplog.at_level(logging.WARNING):
        count = s.append(rec(title="被鎖兩次"))
    assert count == 2
    assert [r[3] for r in rows(tmp_path)] == ["先寫一筆", "被鎖兩次"]
    assert state["calls"] == 3
    assert len(sleeps) == 2
    assert any("Excel" in m for m in caplog.messages)  # 終端機有警告
    assert not (tmp_path / "pending_records.jsonl").exists()


def test_still_locked_after_all_retries_goes_to_pending_and_does_not_raise(monkeypatch, tmp_path, caplog):
    s = store(tmp_path, retries=3)
    s.append(rec(title="先寫一筆"))
    lock_replace(monkeypatch, 999)
    with caplog.at_level(logging.WARNING):
        count = s.append(rec(title="寫不進去"))
    assert count == 2  # 回覆仍然要有正確的當日數
    pending = (tmp_path / "pending_records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(pending) == 1 and json.loads(pending[0])["title"] == "寫不進去"
    assert [r[3] for r in rows(tmp_path)] == ["先寫一筆"]  # 正式檔沒被弄壞
    assert not list(tmp_path.glob("*.tmp"))


def test_pending_records_are_flushed_first_on_next_successful_write(monkeypatch, tmp_path):
    s = store(tmp_path, retries=2)
    s.append(rec(title="A"))
    monkeypatch.setattr(storage.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(13, "x")))
    s.append(rec(title="B-暫存"))
    assert s.append(rec(title="C-暫存")) == 3  # 當日數把暫存的也算進去
    monkeypatch.undo()
    count = s.append(rec(title="D"))
    assert [r[3] for r in rows(tmp_path)] == ["A", "B-暫存", "C-暫存", "D"]
    assert [r[9] for r in rows(tmp_path)] == [1, 2, 3, 4]
    assert count == 4
    assert not (tmp_path / "pending_records.jsonl").exists()


def test_pending_record_keeps_album_and_release_type(monkeypatch, tmp_path):
    s = store(tmp_path, retries=1)
    lock_replace(monkeypatch, 1)
    s.append(rec(album_title="暫存專輯", release_type="單曲"))
    monkeypatch.undo()
    s.append(rec())
    first = rows(tmp_path)[0]
    assert first[4] == "暫存專輯" and first[5] == "單曲"


def test_old_pending_lines_without_the_new_fields_still_load(tmp_path):
    """舊版留下的暫存檔沒有專輯名稱與發行類型，不能因此讀不進來。"""
    old_line = {
        "taken_at": D1.isoformat(),
        "uploader": "舊的人",
        "image_type": "專輯封面",
        "title": "舊暫存的歌",
        "artist": "舊歌手",
        "genre": "Pop",
        "lyrics": "",
        "photo_rel": None,
        "confidence": "高",
    }
    (tmp_path / "pending_records.jsonl").write_text(json.dumps(old_line, ensure_ascii=False) + "\n", encoding="utf-8")
    store(tmp_path).append(rec(title="新的"))
    result = rows(tmp_path)
    assert [r[3] for r in result] == ["舊暫存的歌", "新的"]
    assert result[0][4] == "不明" and result[0][5] == "不明"


def test_locked_on_first_ever_write_keeps_the_record_in_pending(monkeypatch, tmp_path):
    lock_replace(monkeypatch, 999)
    count = store(tmp_path, retries=2).append(rec())
    assert count == 1
    assert (tmp_path / "pending_records.jsonl").exists()
    assert not (tmp_path / "music_records.xlsx").exists()


def test_corrupt_workbook_does_not_crash_and_is_left_untouched(tmp_path, caplog):
    path = tmp_path / "music_records.xlsx"
    path.write_bytes(b"this is not a zip file")
    with caplog.at_level(logging.ERROR):
        count = store(tmp_path).append(rec())
    assert isinstance(count, int)
    assert path.read_bytes() == b"this is not a zip file"
    assert (tmp_path / "pending_records.jsonl").exists()


def test_unrelated_workbook_content_is_preserved(tmp_path):
    """使用者自己在檔案裡加的東西（例如另一張工作表）不能被吃掉。"""
    path = tmp_path / "music_records.xlsx"
    wb = Workbook()
    wb.active.title = "紀錄"
    wb.active.append(COLUMNS)
    wb.create_sheet("我的筆記")["A1"] = "手寫的備註"
    wb.save(path)
    store(tmp_path).append(rec())
    wb2 = load_workbook(path)
    assert "我的筆記" in wb2.sheetnames
    assert wb2["我的筆記"]["A1"].value == "手寫的備註"
    assert wb2["紀錄"].max_row == 2


# ---------- 舊的 10 欄格式自動轉成新的 12 欄 ----------


def make_legacy_file(tmp_path):
    """做一個舊格式（專輯/歌曲名稱合成一欄）的 Excel，含兩列與一個照片連結。"""
    path = tmp_path / "music_records.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "紀錄"
    ws.append(LEGACY_COLUMNS_V1)
    ws.append([D1, "舊使用者", "專輯封面", "嗨！人間（《小深情》）", "周深", "華語流行 / 抒情", "", 1, "photos/舊照片.jpg", "中"])
    ws.append([D1, "舊使用者", "非音樂相關", "手搖飲料", "無", "無", "一杯珍珠奶茶", 1, "photos/飲料.jpg", "無"])
    ws.cell(row=2, column=9).hyperlink = "photos/舊照片.jpg"
    ws.cell(row=3, column=9).hyperlink = "photos/飲料.jpg"
    wb.save(path)
    return path


def test_legacy_layout_has_ten_columns_and_the_old_names():
    assert len(LEGACY_COLUMNS_V1) == 10
    assert LEGACY_COLUMNS_V1[3] == "專輯/歌曲名稱"


def test_legacy_file_is_converted_to_the_new_layout_on_next_append(tmp_path):
    path = make_legacy_file(tmp_path)
    store(tmp_path).append(rec(title="新紀錄"))
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == COLUMNS
    assert ws.max_row == 4


def test_converted_old_rows_keep_their_data_and_get_unknown_album_and_release(tmp_path):
    make_legacy_file(tmp_path)
    store(tmp_path).append(rec(title="新紀錄"))
    old_music, old_drink, new = rows(tmp_path)
    assert old_music[1:4] == ["舊使用者", "專輯封面", "嗨！人間（《小深情》）"]
    assert old_music[4:6] == ["不明", "不明"]
    assert old_music[6:9] == ["周深", "華語流行 / 抒情", None]
    assert old_music[9] == 1 and old_music[11] == "中"
    assert old_drink[2:4] == ["非音樂相關", "手搖飲料"]
    assert old_drink[8] == "一杯珍珠奶茶"
    assert new[3] == "新紀錄" and new[4] == "某專輯" and new[5] == "EP"


def test_converted_photo_hyperlinks_move_to_the_new_photo_column(tmp_path):
    path = make_legacy_file(tmp_path)
    store(tmp_path).append(rec())
    ws = load_workbook(path).active
    assert ws.cell(row=2, column=11).value == "photos/舊照片.jpg"
    assert ws.cell(row=2, column=11).hyperlink.target == "photos/舊照片.jpg"
    assert ws.cell(row=3, column=11).hyperlink.target == "photos/飲料.jpg"
    # 舊的位置（現在是歌詞/文字欄）不能還掛著連結
    assert ws.cell(row=2, column=9).hyperlink is None
    assert ws.cell(row=3, column=9).hyperlink is None


def test_old_rows_still_count_toward_the_same_days_total(tmp_path):
    make_legacy_file(tmp_path)  # 舊檔案裡 D1 那天已經有 1 筆音樂
    assert store(tmp_path).append(rec(taken_at=D1)) == 2


def test_conversion_happens_only_once(tmp_path):
    path = make_legacy_file(tmp_path)
    s = store(tmp_path)
    s.append(rec(title="第一次"))
    s.append(rec(title="第二次"))
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == COLUMNS
    assert ws.max_column == 13 and ws.max_row == 5


def test_a_file_with_an_unknown_header_is_left_alone(tmp_path):
    """標題列既不是新格式也不是舊格式（使用者自己改過），就不去動它。"""
    path = tmp_path / "music_records.xlsx"
    wb = Workbook()
    wb.active.append(["我自己的", "欄位"])
    wb.save(path)
    store(tmp_path).append(rec())
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]][:2] == ["我自己的", "欄位"]
    assert ws.max_column >= 12  # 照樣把紀錄追加在後面


# ---------- 暫存檔與鎖定的邊角 ----------


def test_pending_write_failure_never_raises(monkeypatch, tmp_path, caplog):
    s = store(tmp_path, retries=1)
    lock_replace(monkeypatch, 999)
    real_open = open

    def open_but_pending_is_broken(file, mode="r", *a, **k):
        if str(file).endswith("pending_records.jsonl") and "a" in mode:
            raise OSError("disk full")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", open_but_pending_is_broken)
    with caplog.at_level(logging.ERROR):
        count = s.append(rec())
    assert isinstance(count, int)
    assert any("暫存檔也寫不進去" in m for m in caplog.messages)


def test_failing_to_remove_the_pending_file_after_a_good_save_does_not_duplicate_rows(monkeypatch, tmp_path):
    s = store(tmp_path, retries=3)
    s.append(rec(title="A", photo_rel="photos/a.jpg"))
    lock_replace(monkeypatch, 1)
    s.append(rec(title="B-暫存", photo_rel="photos/b.jpg"))  # 鎖一次、重試成功；先造出暫存
    monkeypatch.undo()
    # 讓下一次寫入：存檔成功，但暫存檔刪不掉
    pending = tmp_path / "pending_records.jsonl"
    pending.write_text(
        json.dumps({**{"taken_at": D1.isoformat(), "uploader": "小明", "image_type": "專輯封面", "title": "C-暫存",
                       "artist": "x", "genre": "Pop", "lyrics": "", "photo_rel": "photos/c.jpg", "confidence": "高"}}) + "\n",
        encoding="utf-8",
    )
    real_unlink = type(pending).unlink

    def stuck_unlink(self, *a, **k):
        if self.name == "pending_records.jsonl":
            raise PermissionError(13, "held by antivirus")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(type(pending), "unlink", stuck_unlink)
    s.append(rec(title="D", photo_rel="photos/d.jpg"))
    s.append(rec(title="E", photo_rel="photos/e.jpg"))  # 暫存檔還在：C 不能再被寫第二次
    titles = [r[3] for r in rows(tmp_path)]
    assert titles.count("C-暫存") == 1
    assert titles.count("D") == 1
    assert titles == ["A", "B-暫存", "C-暫存", "D", "E"]


def test_fallback_count_includes_rows_already_in_excel(monkeypatch, tmp_path):
    s = store(tmp_path, retries=1)
    s.append(rec(title="A", photo_rel="photos/a.jpg"))
    s.append(rec(title="B", photo_rel="photos/b.jpg"))

    def unreadable(*a, **k):
        raise PermissionError(13, "locked for reading")

    monkeypatch.setattr(storage, "load_workbook", unreadable)
    assert s.append(rec(title="C", photo_rel="photos/c.jpg")) == 3  # 不是 1


def test_unreadable_pending_lines_are_kept_in_a_side_file(tmp_path):
    (tmp_path / "pending_records.jsonl").write_text("這行壞掉了\n", encoding="utf-8")
    store(tmp_path).append(rec())
    assert not (tmp_path / "pending_records.jsonl").exists()
    rejected = (tmp_path / "pending_records.rejected.jsonl").read_text(encoding="utf-8")
    assert "這行壞掉了" in rejected


def test_photo_hyperlink_target_escapes_hash_and_percent(tmp_path):
    rel = "photos/2026-10-21 14-03-22 C#100%.jpg"
    store(tmp_path).append(rec(photo_rel=rel))
    cell = load_workbook(tmp_path / "music_records.xlsx").active["K2"]
    assert cell.value == rel  # 看得到的文字維持原樣
    assert "#" not in cell.hyperlink.target and "%23" in cell.hyperlink.target
    assert "%25" in cell.hyperlink.target


# ---------- 上傳者 ID（M 欄）與「你今天已紀錄幾首」 ----------

BIG_ID = "1234567890123456789"  # Discord ID 有 19 位，超過 Excel 數字的 15 位精度，一定要存成文字


def test_uploader_id_is_stored_as_text_in_column_m(tmp_path):
    store(tmp_path).append(rec(uploader_id=BIG_ID))
    ws = load_workbook(tmp_path / "music_records.xlsx").active
    assert ws["M1"].value == "上傳者ID"
    assert ws["M2"].value == BIG_ID
    assert isinstance(ws["M2"].value, str)


def test_record_without_uploader_id_leaves_column_m_empty(tmp_path):
    store(tmp_path).append(rec())
    assert rows(tmp_path)[0][12] in (None, "")


def test_existing_12_column_file_gets_the_new_header_on_next_write(tmp_path):
    path = tmp_path / "music_records.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "紀錄"
    ws.append(COLUMNS[:12])
    ws.append([D1, "舊的人", "專輯封面", "舊歌", "舊專輯", "單曲", "舊歌手", "Pop", "", 1, "photos/old.jpg", "高"])
    wb.save(path)
    store(tmp_path).append(rec(title="新歌", uploader_id=BIG_ID))
    result = rows(tmp_path)
    assert [c.value for c in load_workbook(path).active[1]] == COLUMNS
    assert [r[3] for r in result] == ["舊歌", "新歌"]
    assert result[0][12] in (None, "") and result[1][12] == BIG_ID
    assert result[1][9] == 2  # 當日數照樣接著算


def test_append_record_returns_per_user_counts_for_the_day(tmp_path):
    s = store(tmp_path)
    a = s.append_record(rec(uploader_id="111"))
    b = s.append_record(rec(uploader_id="222"))
    c = s.append_record(rec(uploader_id="111"))
    assert (a.daily_count, a.user_count) == (1, 1)
    assert (b.daily_count, b.user_count) == (2, 1)
    assert (c.daily_count, c.user_count) == (3, 2)


def test_non_music_and_failure_show_the_users_current_count_without_adding(tmp_path):
    s = store(tmp_path)
    s.append_record(rec(uploader_id="111"))
    r = s.append_record(rec(uploader_id="111", image_type="非音樂相關"))
    f = s.append_record(rec(uploader_id="111", image_type="處理失敗"))
    assert r.user_count == 1 and f.user_count == 1


def test_user_count_resets_on_a_new_day_and_ignores_other_days(tmp_path):
    s = store(tmp_path)
    s.append_record(rec(uploader_id="111"))
    r = s.append_record(rec(uploader_id="111", taken_at=D2))
    assert r.user_count == 1 and r.daily_count == 1


def test_user_count_is_none_when_the_record_has_no_uploader_id(tmp_path):
    s = store(tmp_path)
    assert s.append_record(rec()).user_count is None


def test_old_rows_without_an_id_are_not_counted_for_anyone(tmp_path):
    s = store(tmp_path)
    s.append_record(rec())  # 沒有 ID 的舊紀錄
    r = s.append_record(rec(uploader_id="111"))
    assert r.daily_count == 2 and r.user_count == 1


def test_append_still_returns_just_the_daily_count(tmp_path):
    assert store(tmp_path).append(rec(uploader_id="111")) == 1


def test_pending_record_keeps_the_uploader_id(monkeypatch, tmp_path):
    s = store(tmp_path, retries=1)
    lock_replace(monkeypatch, 1)
    first = s.append_record(rec(uploader_id="111"))
    assert first.user_count == 1  # 暫存的也算進去
    monkeypatch.undo()
    s.append_record(rec(uploader_id="111"))
    assert [r[12] for r in rows(tmp_path)] == ["111", "111"]


# ---------- 統計（給「!統計」指令用） ----------


def test_stats_counts_music_only_and_ranks_artists_and_genres(tmp_path):
    s = store(tmp_path)
    s.append(rec(uploader_id="111", artist="優里", genre="J-Pop / 抒情"))
    s.append(rec(uploader_id="111", artist="優里", genre="J-Pop"))
    s.append(rec(uploader_id="222", artist="YOASOBI", genre="J-Pop / 電子"))
    s.append(rec(uploader_id="222", image_type="非音樂相關", artist="無", genre="無"))
    s.append(rec(uploader_id="222", image_type="處理失敗", artist="無", genre="無"))
    s.append(rec(uploader_id="222", artist="不明", genre="無"))
    st = s.stats(today=D1.date(), uploader_id="111")
    assert st.total_music == 4 and st.today_music == 4
    assert st.uploaders == 2
    assert st.user_today == 2 and st.user_total == 2
    assert st.top_artists[0] == ("優里", 2)
    assert ("YOASOBI", 1) in st.top_artists
    assert all(name not in ("無", "不明") for name, _ in st.top_artists)
    assert st.top_genres[0] == ("J-Pop", 3)
    assert ("抒情", 1) in st.top_genres and all(name != "無" for name, _ in st.top_genres)


def test_stats_separates_today_from_the_total(tmp_path):
    s = store(tmp_path)
    s.append(rec(uploader_id="111"))
    s.append(rec(uploader_id="111", taken_at=D2))
    st = s.stats(today=D2.date(), uploader_id="111")
    assert (st.total_music, st.today_music, st.user_today, st.user_total) == (2, 1, 1, 2)


def test_stats_without_an_uploader_id_has_no_personal_numbers(tmp_path):
    s = store(tmp_path)
    s.append(rec(uploader_id="111"))
    st = s.stats(today=D1.date())
    assert st.user_today is None and st.user_total is None


def test_stats_on_a_missing_file_is_all_zero(tmp_path):
    st = store(tmp_path).stats(today=D1.date(), uploader_id="111")
    assert (st.total_music, st.today_music, st.uploaders) == (0, 0, 0)
    assert st.top_artists == [] and st.top_genres == []
