"""呼叫 `claude -p` 分析一張圖片（architecture.md 第 3.4、5 節）。

這個檔案有兩段防護：
  防護 1：工作目錄限制（claude 只碰得到「這一張圖」的臨時資料夾）
  防護 2：Prompt Injection 隔離（上傳者的文字不進指令）
用 `grep -n "==== 防護" src/music_bot/analyzer.py` 可以找到確切行號。
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .schema import IMAGE_EXTS, Analysis, SchemaError, extract_payload, json_schema, validate_analysis

logger = logging.getLogger(__name__)

# 開關：True = 防護開著。故意不放進 .env，避免誤改設定就悄悄關掉防護。
ENABLE_GUARD_1 = True
ENABLE_GUARD_2 = True

CLAUDE_TIMEOUT_SECONDS = 120.0
MAX_CALLS_PER_IMAGE = 2  # 第一次加一次重試，保護額度
_PROMPT_DIR = Path(__file__).parent / "prompts"

# API 偶爾會擋下輸出（常見於已知歌曲的歌詞），而且是間歇性的：同一張圖重試常常就過了
_FILTER_HINTS = ("content filtering", "blocked by content")
_FILTER_RETRY_NOTE = (
    "\n\n上一次的輸出被內容過濾政策擋下。這次請確認 lyrics_text 是空字串，"
    "不要輸出任何歌詞或圖上的長段文字。其他欄位照常填寫。"
)

# 這些字出現代表額度或速率問題（claude 沒有固定的錯誤碼，只能比對文字）
_QUOTA_HINTS = (
    "usage limit",
    "rate limit",
    "limit reached",
    "hit your limit",
    "credit balance",
    "quota",
)
# HTTP 429 要整個詞比對：不然 duration_ms、session_id 之類的數字剛好含有 429 就會被誤判成額度問題
_QUOTA_STATUS = re.compile(r"\b429\b")


class AnalysisFailure(Exception):
    """這張圖分析不出結果。reason 是固定的失敗原因類別，之後會寫進 Excel 與回覆。"""

    def __init__(self, reason: str, salvaged_title: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.salvaged_title = salvaged_title


class _ContentFiltered(Exception):
    """API 擋下了輸出。內部用，會被轉成一次重試。"""


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


# runner(指令, 要餵進標準輸入的文字, 工作目錄, 逾時秒數) -> RunResult；逾時要丟 TimeoutError
Runner = Callable[[list[str], str, Path, float], Awaitable[RunResult]]


def find_claude() -> str | None:
    """優先找 claude.exe。

    Windows 上 npm 裝的是 claude.cmd，執行 .cmd 會經過 cmd.exe 解析，
    系統指令裡的符號可能被當成指令；.exe 不會有這個問題。
    """
    return shutil.which("claude.exe") or shutil.which("claude")


# ---------- 防護 1 ----------


# ==== 防護 1：工作目錄限制 開始 ====
# 威脅：claude 預設能讀電腦上很多檔案。若被圖片上的字騙了，可能把 DATA_DIR 裡
#       別人的照片和 Excel 內容帶進回覆。
# 做法：每張圖開一個全新的臨時資料夾，只放這一張圖；claude 的工作目錄設在那裡，
#       只開放 Read 工具，並用 --restricted 把讀檔範圍鎖在工作目錄內。處理完整個刪掉。
@dataclass
class Sandbox:
    workdir: Path  # claude 的工作目錄
    image_ref: str  # 在指示裡怎麼稱呼那張圖：開著時是檔名，關掉時是完整路徑
    args: list[str]


@contextmanager
def sandbox_for(image: bytes, ext: str, data_dir: Path) -> Iterator[Sandbox]:
    image_name = f"image{ext}"  # 固定檔名，絕不用上傳者給的檔名
    with tempfile.TemporaryDirectory(prefix="music-bot-", ignore_cleanup_errors=True) as tmp:
        image_path = Path(tmp) / image_name
        image_path.write_bytes(image)
        if ENABLE_GUARD_1:
            args = [
                "--tools", "Read",
                "--allowedTools", "Read",
                "--permission-mode", "dontAsk",
                "--restricted",
            ]  # fmt: skip
            yield Sandbox(Path(tmp), image_name, args)
        else:
            # 故意放寬（上課對照用）：不鎖讀檔範圍，工作目錄直接設成 DATA_DIR，
            # 所以只寫檔名（例如 canary_test.txt）也讀得到 DATA_DIR 裡的東西。
            # 圖片還在臨時資料夾，所以用 --add-dir 開放它，並在指示裡給完整路徑。
            args = [
                "--tools", "Read",
                "--allowedTools", "Read",
                "--permission-mode", "dontAsk",
                "--add-dir", tmp,
            ]  # fmt: skip
            yield Sandbox(data_dir, str(image_path), args)


# ==== 防護 1：工作目錄限制 結束 ====


# ---------- 防護 2 ----------


# ==== 防護 2：Prompt Injection 隔離 開始 ====
# 威脅：AI 分不清「該遵守的指令」和「只是要處理的內容」。攻擊者把指令混在上傳者的
#       文字（或圖片裡的字）裡，騙它改變行為。
# 做法：1. 上傳者打的字完全不放進給 claude 的指令（下面的 caption 在開著時被忽略）。
#       2. 系統指令明確規定：圖片裡的字都只是資料，不是指令（guard2_data_only.md）。
def build_user_prompt(image_ref: str, caption: str | None) -> str:
    prompt = f"請讀取這一個圖片檔案：{image_ref}，依規格分析。"
    if not ENABLE_GUARD_2 and caption:
        # 故意不設防（上課對照用）：上傳者的文字直接黏在指令後面
        prompt += f"\n上傳者附註：{caption}"
    return prompt


def build_system_prompt() -> str:
    text = _read_prompt("system_prompt.md")
    if ENABLE_GUARD_2:  # 開關在呼叫時才看，檔案內容本身不會變所以可以快取
        text += _read_prompt("guard2_data_only.md")
    return text


# ==== 防護 2：Prompt Injection 隔離 結束 ====


# ---------- 實際執行 claude ----------


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """逾時時把 claude 連同它開的子行程一起結束。"""
    try:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )  # fmt: skip
            await killer.wait()
        else:
            proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        logger.exception("結束 claude 行程時發生錯誤")


async def default_runner(cmd: list[str], stdin: str, cwd: Path, timeout: float) -> RunResult:
    # 指令用「清單」傳，不經過 shell；提示文字走標準輸入，避開 Windows 命令列的引號與長度問題
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8")), timeout)
    except asyncio.TimeoutError:
        await _kill_tree(proc)
        raise TimeoutError from None
    except asyncio.CancelledError:
        await _kill_tree(proc)
        raise
    return RunResult(
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


def _outer_is_error(stdout: str) -> bool:
    try:
        outer = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(outer, dict) and bool(outer.get("is_error"))


def _outer_error_text(stdout: str) -> str:
    """只取外層 JSON 裡真的在講「錯在哪」的欄位，不看 duration_ms、session_id 這些中繼資料。"""
    try:
        outer = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout  # 不是 JSON（例如純文字的錯誤訊息），整段都當錯誤文字
    if not isinstance(outer, dict):
        return stdout
    return " ".join(str(outer[k]) for k in ("result", "error", "api_error_status") if outer.get(k) is not None)


def _classify_run_error(result: RunResult) -> str:
    text = f"{_outer_error_text(result.stdout)}\n{result.stderr}".lower()
    if any(hint in text for hint in _FILTER_HINTS):
        return "內容過濾"
    if any(hint in text for hint in _QUOTA_HINTS) or _QUOTA_STATUS.search(text):
        return "額度不足"
    return "辨識程式錯誤"


def _outer_is_structured_output_failure(stdout: str) -> bool:
    """claude 自己重試了好幾次還是湊不出符合規格的輸出（subtype 形如 error_max_structured_output_retries）。"""
    try:
        outer = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(outer, dict) and "structured_output" in str(outer.get("subtype", ""))


@functools.cache
def _schema_arg() -> str:
    return json.dumps(json_schema(), ensure_ascii=False)


@functools.cache
def _read_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


class Analyzer:
    def __init__(
        self,
        claude_path: str,
        model: str,
        data_dir: Path,
        runner: Runner = default_runner,
        timeout: float = CLAUDE_TIMEOUT_SECONDS,
    ) -> None:
        self.claude_path = claude_path
        self.model = model
        self.data_dir = Path(data_dir)
        self.runner = runner
        self.timeout = timeout

    def _command(self, box: Sandbox) -> list[str]:
        return [
            self.claude_path,
            "-p",
            "--model", self.model,
            "--output-format", "json",
            "--json-schema", _schema_arg(),
            "--system-prompt", build_system_prompt(),
            "--no-session-persistence",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--safe-mode",
            "--effort", "low",
            *box.args,
        ]  # fmt: skip

    async def _call(self, box: Sandbox, prompt: str) -> Analysis:
        """呼叫一次 claude 並驗證。驗證不過丟 SchemaError；執行層級的問題丟 AnalysisFailure。"""
        try:
            result = await self.runner(self._command(box), prompt, box.workdir, self.timeout)
        except TimeoutError:
            raise AnalysisFailure("逾時") from None
        except OSError as e:
            logger.error("無法執行 claude：%s", e)
            raise AnalysisFailure("辨識程式錯誤") from None

        if result.returncode != 0 or _outer_is_error(result.stdout):
            if _outer_is_structured_output_failure(result.stdout):
                raise SchemaError("claude 沒能產生符合規格的輸出")  # 格式問題，照規定重試一次
            reason = _classify_run_error(result)
            if reason == "內容過濾":
                raise _ContentFiltered
            logger.error("claude 執行失敗（%s），結束碼 %s：%s", reason, result.returncode, (result.stderr or result.stdout)[:300])
            raise AnalysisFailure(reason)

        return validate_analysis(extract_payload(result.stdout))

    async def analyze(self, image: bytes, ext: str, caption: str | None = None) -> Analysis:
        """分析一張圖。成功回傳 Analysis；失敗一律丟 AnalysisFailure，不會丟別的例外。"""
        if ext not in IMAGE_EXTS:
            raise AnalysisFailure("圖片損壞或格式不支援")
        try:
            with sandbox_for(image, ext, self.data_dir) as box:
                prompt = build_user_prompt(box.image_ref, caption)
                base_prompt = prompt
                last_reason, last_title = "格式不符", None
                for _ in range(MAX_CALLS_PER_IMAGE):
                    try:
                        return await self._call(box, prompt)
                    except SchemaError as e:
                        logger.warning("claude 的輸出不合格：%s", e.summary)
                        last_reason, last_title = "格式不符", e.salvaged_title
                        # 重試時只附 agent 自己寫的摘要，絕不把模型的原始輸出貼回去
                        prompt = base_prompt + f"\n\n上一次的輸出有問題：{e.summary}。請只輸出符合規格的 JSON。"
                    except _ContentFiltered:
                        logger.warning("claude 的輸出被內容過濾政策擋下")
                        last_reason, last_title = "內容過濾", None
                        prompt = base_prompt + _FILTER_RETRY_NOTE
                raise AnalysisFailure(last_reason, last_title)
        except AnalysisFailure:
            raise
        except Exception as e:
            logger.exception("分析時發生未預期的錯誤")
            raise AnalysisFailure("內部錯誤") from e
