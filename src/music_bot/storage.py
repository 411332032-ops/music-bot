"""照片與 Excel 的本機儲存（architecture.md 第 8、9 節）。

這個檔案有兩段防護：
  防護 3：檔名安全性清洗（照片檔名）
  防護 4：Excel 公式注入防護（寫進儲存格的文字）
用 `grep -n "==== 防護" src/music_bot/storage.py` 可以找到確切行號。
"""

from __future__ import annotations

import json
import logging
import os
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

from .schema import IMAGE_EXTS, MUSIC_TYPES, UNKNOWN

logger = logging.getLogger(__name__)

# 開關：True = 防護開著。故意不放進 .env，避免誤改設定就悄悄關掉防護。
ENABLE_GUARD_3 = True
ENABLE_GUARD_4 = True

PLACEHOLDER_NAME = "未命名"
MAX_NAME_LEN = 30
_MAX_COLLISIONS = 1000


# ==== 防護 3：檔名安全性清洗 開始 ====
# 威脅：專輯/歌曲名稱來自 AI，而 AI 看的是別人的圖。名稱裡若有 ..\ / : 等字，
# 存檔時檔案可能跑到 photos/ 之外，或在 Windows 上直接寫入失敗。
_FORBIDDEN_CHARS = '/\\:*?"<>|'
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"}
_RESERVED_NAMES |= {f"COM{i}" for i in range(1, 10)}
_RESERVED_NAMES |= {f"LPT{i}" for i in range(1, 10)}


def sanitize_filename_part(name: str, max_len: int = MAX_NAME_LEN) -> str:
    """把任意文字清成可安全放進檔名的簡稱。"""
    if not ENABLE_GUARD_3:
        return name

    # 1. 去掉控制字元（換行、tab、不可見字元）
    cleaned = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C"))
    # 2. Windows 不允許、或會讓路徑亂跑的字元換成底線
    cleaned = "".join("_" if ch in _FORBIDDEN_CHARS else ch for ch in cleaned)
    # 3. ".." 代表上一層資料夾，整個去掉（反覆去，避免 "...." 去掉後又拼出 ".."）
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "")
    # 4. 頭尾的空白與句點 Windows 會自動吃掉，先去掉
    cleaned = cleaned.strip(" .")
    # 5. 截短，截完可能又留下尾端空白或句點
    cleaned = cleaned[:max_len].rstrip(" .")
    if not cleaned:
        return PLACEHOLDER_NAME
    # 6. Windows 保留名稱不能當檔名（CON、NUL、COM1 ...）
    if cleaned.split(".")[0].rstrip().upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def ensure_inside_dir(path: Path, base_dir: Path) -> None:
    """最後一道保險：組好的完整路徑，最終位置必須真的在 base_dir 裡面。"""
    if not ENABLE_GUARD_3:
        return
    if path.resolve().parent != base_dir.resolve():
        raise ValueError("檔案路徑跑出照片資料夾，已拒絕寫入")


# ==== 防護 3：檔名安全性清洗 結束 ====


def detect_image_ext(data: bytes) -> str | None:
    """看檔案開頭的魔術數字判斷真正的格式，不相信上傳者給的副檔名。"""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_photo(
    photos_dir: Path | str,
    data: bytes,
    taken_at: datetime,
    short_name: str,
    ext: str,
) -> Path:
    """存照片。檔名：`YYYY-MM-DD hh-mm-ss 簡稱.副檔名`，撞名依序加 -2、-3。"""
    photos_dir = Path(photos_dir)
    photos_dir.mkdir(parents=True, exist_ok=True)
    if ext not in IMAGE_EXTS:
        ext = ".bin"
    stem = f"{taken_at:%Y-%m-%d %H-%M-%S} {sanitize_filename_part(short_name)}"

    for n in range(1, _MAX_COLLISIONS + 1):
        suffix = "" if n == 1 else f"-{n}"
        path = photos_dir / f"{stem}{suffix}{ext}"
        ensure_inside_dir(path, photos_dir)
        try:
            # "x" 模式：只在檔案不存在時才建立，絕不覆蓋既有檔案
            with open(path, "xb") as f:
                f.write(data)
        except FileExistsError:
            continue
        return path
    raise OSError("同名照片太多，無法產生不重複的檔名")


# =====================================================================
# Excel 紀錄
# =====================================================================

COLUMNS = [
    "日期時間",
    "上傳者",
    "圖片類型",
    "歌曲名稱",
    "專輯名稱",  # 專輯、EP 或單曲的名稱；發行類型寫在下一欄
    "發行類型",
    "歌手/樂團",
    "曲風估算",
    "辨識歌詞/文字內容",
    "當日已紀錄音樂數",
    "照片存放位置",
    "信心程度",
    "上傳者ID",  # Discord 的使用者 ID（文字）：顯示名稱可以改，ID 不會變，統計「你」用它
]
_PHOTO_COLUMN = 11  # 「照片存放位置」是第幾欄（從 1 起算）
_UPLOADER_ID_COLUMN = 13
_COLUMN_WIDTHS = [20, 14, 14, 28, 28, 10, 20, 20, 40, 12, 45, 10, 22]

# 舊版（歌名與專輯名稱合成「專輯/歌曲名稱」一欄）的 10 欄格式。打開舊檔案時會自動轉成上面的新格式。
LEGACY_COLUMNS_V1 = [
    "日期時間",
    "上傳者",
    "圖片類型",
    "專輯/歌曲名稱",
    "歌手/樂團",
    "曲風估算",
    "辨識歌詞/文字內容",
    "當日已紀錄音樂數",
    "照片存放位置",
    "信心程度",
]
_LEGACY_PHOTO_COLUMN = 9
_LEGACY_INSERT_AT = 5  # 在舊格式的第 5 欄（歌手）前面插入「專輯名稱」「發行類型」兩欄
SHEET_NAME = "紀錄"
MAX_CELL_CHARS = 32000  # Excel 單一儲存格上限是 32767
_NOT_A_NAME = ("無", UNKNOWN, "無法辨識")  # 統計歌手、曲風排行時要跳過的「沒有名稱」
PENDING_FILENAME = "pending_records.jsonl"
REJECTED_PENDING_FILENAME = "pending_records.rejected.jsonl"  # 暫存檔裡讀不懂的行另存在這裡


# ==== 防護 4：Excel 公式注入防護 開始 ====
# 威脅：文字以 = + - @ 開頭時，Excel 打開可能把它當公式執行（例如連到惡意網址）。
# 做法：在前面加一個單引號 '，讓它變成純文字。
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n")


def neutralize_formula(value: str) -> str:
    if not ENABLE_GUARD_4:
        return value
    if value.lstrip(" ").startswith(_FORMULA_STARTS):
        return "'" + value
    return value


# ==== 防護 4：Excel 公式注入防護 結束 ====


def clean_cell_text(value: str) -> str:
    """寫進儲存格的文字。基本清洗永遠開著；公式防護見上面防護 4。"""
    # 基本清洗（不屬於防護）：Excel 不接受的控制字元會讓 openpyxl 直接報錯；長度有上限
    value = ILLEGAL_CHARACTERS_RE.sub("", value)[:MAX_CELL_CHARS]
    return neutralize_formula(value)


@dataclass
class Record:
    taken_at: datetime  # 上傳時間（本機時區）；補處理時也用原本的上傳時間
    uploader: str
    image_type: str
    title: str
    artist: str
    genre: str
    lyrics: str
    photo_rel: str | None  # 相對於 DATA_DIR 的路徑，例如 photos/xxx.jpg
    confidence: str
    # 後來新增的欄位，放最後面並給預設值：舊版留下的暫存檔沒有這兩個欄位，也要能讀進來
    album_title: str = UNKNOWN
    release_type: str = UNKNOWN
    uploader_id: str = ""  # Discord 使用者 ID；舊的暫存檔與舊的紀錄沒有，就是空字串


@dataclass(frozen=True)
class AppendResult:
    daily_count: int  # 這一天（上傳那天）全頻道的音樂數
    user_count: int | None  # 這一天這個人的音樂數；沒有上傳者 ID 就是 None


@dataclass(frozen=True)
class Stats:
    total_music: int
    today_music: int
    uploaders: int  # 有幾個不同的上傳者（只算音樂相關的紀錄）
    user_today: int | None  # 沒給上傳者 ID 就是 None
    user_total: int | None
    top_artists: list[tuple[str, int]]
    top_genres: list[tuple[str, int]]


class _Added(NamedTuple):
    daily: int
    user: int | None
    day_counts: dict[date, int]
    user_counts: dict[tuple[date, str], int]


class ExcelStore:
    """把紀錄一列一列追加到同一個 xlsx。

    xlsx 不能像文字檔直接接在尾巴，所以做法是：打開 → 在最後一列下面加 → 存成暫存檔 →
    整個換掉正式檔（os.replace）。Windows 上 Excel 正開著這個檔案時換檔會失敗
    （PermissionError），這時重試；仍不行就先存到 pending_records.jsonl，下次一起補寫。
    """

    def __init__(
        self,
        path: Path | str,
        retries: int = 10,
        retry_delay: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = Path(path)
        self.pending_path = self.path.parent / PENDING_FILENAME
        self.retries = retries
        self.retry_delay = retry_delay
        self.sleep = sleep
        self._known_counts: dict[date, int] = {}  # 最近一次成功寫入後，每一天的音樂數
        self._known_user_counts: dict[tuple[date, str], int] = {}  # 同上，每個人每一天
        self._unparsable_pending: list[str] = []  # 這一輪從暫存檔讀不懂的行
        self._loaded_pending: list[str] = []  # 這一輪讀進來、準備補寫的行
        self._already_written: set[str] = set()  # 已經補寫進 Excel、但暫存檔還刪不掉的行

    # ---------- 對外 ----------

    def append(self, record: Record) -> int:
        """追加一筆，回傳這一筆的「當日已紀錄音樂數」。絕不丟出例外。"""
        return self.append_record(record).daily_count

    def append_record(self, record: Record) -> AppendResult:
        """追加一筆，回傳當日音樂數，以及這個人當天的音樂數。絕不丟出例外。"""
        batch = self._load_pending() + [record]
        added: _Added | None = None
        for attempt in range(1, self.retries + 1):
            try:
                wb, ws = self._open()
                added = self._add_rows(ws, batch)
                self._save(wb)
            except PermissionError as e:
                logger.warning(
                    "Excel 檔案寫入失敗（可能正被打開），第 %d/%d 次：%s。請關閉 Excel。",
                    attempt,
                    self.retries,
                    e,
                )
                if attempt < self.retries:
                    self.sleep(self.retry_delay)
            except Exception:
                logger.exception("Excel 讀寫發生非預期錯誤，這筆紀錄改存到暫存檔")
                break
            else:
                # 存檔已經成功；清暫存檔失敗不能當成「Excel 寫入失敗」重來，否則同一批紀錄會被寫兩次
                self._known_counts = added.day_counts
                self._known_user_counts = added.user_counts
                self._clear_pending()
                return AppendResult(added.daily, added.user)

        self._append_pending(record)
        if added is not None:
            return AppendResult(added.daily, added.user)  # 掃描過了，只是存不進去：數字是準的
        # 連 Excel 都讀不到，沒辦法重新掃描：用上次成功寫入後記下的數字，再加上還沒寫進去的
        day, uid = record.taken_at.date(), record.uploader_id
        unwritten = [r for r in batch if r.image_type in MUSIC_TYPES and r.taken_at.date() == day]
        daily = self._known_counts.get(day, 0) + len(unwritten)
        user = None
        if uid:
            user = self._known_user_counts.get((day, uid), 0) + sum(1 for r in unwritten if r.uploader_id == uid)
        return AppendResult(daily, user)

    def stats(self, today: date, uploader_id: str = "", top_n: int = 5) -> Stats:
        """讀 Excel 做統計（只算音樂相關的紀錄）。檔案還不存在就全部是 0；讀不了會丟例外。"""
        total = today_count = user_today = user_total = 0
        uploaders: set[str] = set()
        artists: Counter[str] = Counter()
        genres: Counter[str] = Counter()
        if self.path.exists():
            wb = load_workbook(self.path, read_only=True)
            try:
                for row in wb.worksheets[0].iter_rows(min_row=2, values_only=True):
                    if len(row) < 3 or row[2] not in MUSIC_TYPES:
                        continue
                    day = self._row_date(row[0])
                    uid = self._cell_uploader_id(row)
                    total += 1
                    uploaders.add(uid or str(row[1]))
                    if day == today:
                        today_count += 1
                    if uploader_id and uid == uploader_id:
                        user_total += 1
                        user_today += day == today
                    artist = str(row[6]).strip() if len(row) > 6 and row[6] is not None else ""
                    if artist and artist not in _NOT_A_NAME:
                        artists[artist] += 1
                    genre_cell = str(row[7]) if len(row) > 7 and row[7] is not None else ""
                    for label in genre_cell.split(" / "):
                        label = label.strip()
                        if label and label not in _NOT_A_NAME:
                            genres[label] += 1
            finally:
                wb.close()
        return Stats(
            total_music=total,
            today_music=today_count,
            uploaders=len(uploaders),
            user_today=user_today if uploader_id else None,
            user_total=user_total if uploader_id else None,
            top_artists=artists.most_common(top_n),
            top_genres=genres.most_common(top_n),
        )

    # ---------- 內部：Excel ----------

    @staticmethod
    def _apply_column_widths(ws) -> None:
        for i, width in enumerate(_COLUMN_WIDTHS, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width

    def _open(self):
        if self.path.exists():
            wb = load_workbook(self.path)
            ws = wb.worksheets[0]
            header = [c.value for c in ws[1]]
            if header == LEGACY_COLUMNS_V1:
                self._migrate_legacy(ws)
            elif header == COLUMNS[:-1]:
                self._add_uploader_id_column(ws)
            return wb, ws
        wb = Workbook()
        ws = wb.active
        ws.title = SHEET_NAME
        ws.append(COLUMNS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        self._apply_column_widths(ws)
        ws.freeze_panes = "A2"
        return wb, ws

    def _add_uploader_id_column(self, ws) -> None:
        """12 欄的檔案：在最後面補上「上傳者ID」欄的標題，舊紀錄這一欄留空。"""
        logger.info("偵測到沒有「上傳者ID」欄的 Excel，補上這一欄（舊紀錄留空）")
        header = ws.cell(row=1, column=_UPLOADER_ID_COLUMN)
        header.value = COLUMNS[-1]
        header.font = Font(bold=True)
        self._apply_column_widths(ws)

    def _migrate_legacy(self, ws) -> None:
        """把舊的 10 欄格式轉成新的 12 欄：在歌手前面插入「專輯名稱」「發行類型」兩欄。

        舊紀錄的這兩欄填「不明」，其餘資料與照片連結都保留。
        這個轉換在記憶體裡做，跟著這次的寫入一起存檔；存檔失敗（例如檔案被鎖住）就不會留下半成品。
        """
        logger.info("偵測到舊格式的 Excel，自動轉成新格式（新增「專輯名稱」「發行類型」兩欄）")
        # 照片欄的超連結是綁在儲存格位置上的，欄位移動後舊位置會失效（甚至掛到別的欄），
        # 所以先記下來、全部清掉，移完再掛回新的位置。
        links: dict[int, str] = {}
        for row in ws.iter_rows(min_row=2):
            cell = row[_LEGACY_PHOTO_COLUMN - 1]
            if cell.hyperlink is not None and cell.hyperlink.target:
                links[cell.row] = cell.hyperlink.target
            cell._hyperlink = None  # openpyxl 沒有公開的清除方式
        ws._hyperlinks = []

        ws.insert_cols(_LEGACY_INSERT_AT, 2)

        for col, name in enumerate(COLUMNS, start=1):
            header = ws.cell(row=1, column=col)
            header.value = name
            header.font = Font(bold=True)
        for row_no in range(2, ws.max_row + 1):
            ws.cell(row=row_no, column=_LEGACY_INSERT_AT).value = UNKNOWN
            ws.cell(row=row_no, column=_LEGACY_INSERT_AT + 1).value = UNKNOWN
        for row_no, target in links.items():
            cell = ws.cell(row=row_no, column=_PHOTO_COLUMN)
            cell.hyperlink = target
            cell.style = "Hyperlink"
        self._apply_column_widths(ws)

    @staticmethod
    def _row_date(value) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.strip()).date()
            except ValueError:
                return None
        return None

    @staticmethod
    def _cell_uploader_id(row) -> str:
        value = row[_UPLOADER_ID_COLUMN - 1] if len(row) >= _UPLOADER_ID_COLUMN else None
        return "" if value is None else str(value).strip()

    def _add_rows(self, ws, batch: list[Record]) -> _Added:
        """把 batch 依序加到最後，回傳最後一筆的當日數與個人當日數，以及加完之後每一天、每個人的音樂數。"""
        counts: dict[date, int] = defaultdict(int)
        user_counts: dict[tuple[date, str], int] = defaultdict(int)
        for row in ws.iter_rows(min_row=2, values_only=True):
            d = self._row_date(row[0] if row else None)
            if d is not None and len(row) > 2 and row[2] in MUSIC_TYPES:
                counts[d] += 1
                uid = self._cell_uploader_id(row)
                if uid:
                    user_counts[(d, uid)] += 1

        last = 0
        last_user: int | None = None
        for r in batch:
            day = r.taken_at.date()
            if r.image_type in MUSIC_TYPES:
                counts[day] += 1
                if r.uploader_id:
                    user_counts[(day, r.uploader_id)] += 1
            last = counts[day]
            last_user = user_counts[(day, r.uploader_id)] if r.uploader_id else None
            ws.append(
                [
                    r.taken_at,
                    clean_cell_text(r.uploader),
                    clean_cell_text(r.image_type),
                    clean_cell_text(r.title),
                    clean_cell_text(r.album_title),
                    clean_cell_text(r.release_type),
                    clean_cell_text(r.artist),
                    clean_cell_text(r.genre),
                    clean_cell_text(r.lyrics),
                    last,
                    r.photo_rel if r.photo_rel else "無",
                    clean_cell_text(r.confidence),
                    # 一定要是文字：19 位數字放不進 Excel 的數字格式（只有 15 位精度）
                    clean_cell_text(r.uploader_id) if r.uploader_id else None,
                ]
            )
            row_no = ws.max_row
            ws.cell(row=row_no, column=1).number_format = "yyyy-mm-dd hh:mm:ss"
            if r.photo_rel:
                link = ws.cell(row=row_no, column=_PHOTO_COLUMN)
                # 相對路徑：整個資料夾搬家連結也不會斷。# 和 % 在連結裡有特殊意思
                # （片段、跳脫字元），歌名含這些字時要跳脫，不然點下去找不到檔案；儲存格上看到的文字不變
                link.hyperlink = r.photo_rel.replace("%", "%25").replace("#", "%23")
                link.style = "Hyperlink"
        return _Added(last, last_user, dict(counts), dict(user_counts))

    def _save(self, wb) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            wb.save(tmp)
            os.replace(tmp, self.path)  # Excel 開著時這裡會 PermissionError
        finally:
            tmp.unlink(missing_ok=True)

    # ---------- 內部：暫存 ----------

    def _load_pending(self) -> list[Record]:
        if not self.pending_path.exists():
            return []
        records: list[Record] = []
        self._unparsable_pending = []
        self._loaded_pending = []
        for line in self.pending_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line in self._already_written:
                continue
            try:
                data = json.loads(line)
                data["taken_at"] = datetime.fromisoformat(data["taken_at"])
                records.append(Record(**data))
                self._loaded_pending.append(line)
            except (ValueError, KeyError, TypeError):
                logger.error("暫存檔有一行讀不懂，已略過（清掉暫存檔時會另存到 %s）", REJECTED_PENDING_FILENAME)
                self._unparsable_pending.append(line)
        return records

    def _append_pending(self, record: Record) -> None:
        data = asdict(record)
        data["taken_at"] = record.taken_at.isoformat()
        try:
            with open(self.pending_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except OSError:
            # append 承諾不丟例外；寫不進去也只能留下線索，讓人工補救
            logger.exception("暫存檔也寫不進去，這筆紀錄沒有保存下來：%s %s", record.taken_at, record.photo_rel)
            return
        logger.warning("這筆紀錄已先存到 %s，下次 Excel 可寫入時會自動補上", self.pending_path)

    def _clear_pending(self) -> None:
        if self._unparsable_pending:
            # 讀不懂的行不能跟著暫存檔一起消失，另存起來讓人看
            try:
                with open(self.pending_path.with_name(REJECTED_PENDING_FILENAME), "a", encoding="utf-8") as f:
                    f.write("\n".join(self._unparsable_pending) + "\n")
            except OSError:
                logger.exception("讀不懂的暫存行也無法另存")
            self._unparsable_pending = []
        try:
            self.pending_path.unlink(missing_ok=True)
        except OSError:
            # Excel 已經存好了，這只是善後。記住這些行已經寫過，下次讀暫存檔時略過，才不會重複寫
            self._already_written.update(self._loaded_pending)
            logger.warning("Excel 已寫入，但暫存檔 %s 刪不掉", self.pending_path, exc_info=True)
        else:
            self._already_written.clear()
