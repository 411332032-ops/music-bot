"""啟動入口：`uv run python main.py`（或 `uv run python -m music_bot`）。"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord

from .analyzer import Analyzer, find_claude
from .bot import MusicBotClient
from .config import ConfigError, load_config
from .pipeline import Processor
from .storage import ExcelStore

logger = logging.getLogger("music_bot")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def environment_warnings(environ: Mapping[str, str]) -> list[str]:
    warnings: list[str] = []
    if environ.get("ANTHROPIC_API_KEY"):
        warnings.append(
            "系統環境變數有設定 ANTHROPIC_API_KEY，claude 可能因此改走 API 計費，"
            "而不是你登入的 Claude 帳號額度。如果這不是你要的，請把它移除。"
        )
    return warnings


def explain_startup_error(error: BaseException) -> str | None:
    """把 Discord 的啟動錯誤翻成看得懂的中文；不認得的就回傳 None。"""
    if isinstance(error, discord.LoginFailure):
        return "登入失敗：DISCORD_TOKEN 不正確（複製不完整、多了空白或引號，或已經被 Reset 過）。請到 Developer Portal 重新取得。"
    if isinstance(error, discord.PrivilegedIntentsRequired):
        return (
            "缺少 Message Content Intent：請到 Discord Developer Portal → Bot → "
            "Privileged Gateway Intents，打開 Message Content Intent 並按 Save Changes。"
            "詳見 docs/discord-bot-setup.md 第二部分。"
        )
    return None


def setup_logging(logs_dir: Path) -> None:
    """log 同時印在終端機和寫進 DATA_DIR/logs/agent.log。log 只記狀態，不記 token 與完整歌詞。"""
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(logs_dir / "agent.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    # 「語音功能未安裝」的警告：我們沒用語音，不需要讓使用者看到
    logging.getLogger("discord.client").setLevel(logging.ERROR)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)  # 讀設定前先有基本輸出
    try:
        config = load_config()
    except ConfigError as e:
        logger.error("%s", e)
        sys.exit(1)

    # 設定讀好了，改成同時寫檔的 log（先拿掉 basicConfig 加的那個）
    for handler in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(handler)
    setup_logging(config.logs_dir)

    for warning in environment_warnings(os.environ):
        logger.warning(warning)

    claude_path = find_claude()
    if claude_path is None:
        logger.error("找不到 claude 指令。請先安裝並登入 Claude Code，確認在終端機輸入 claude --version 有反應。")
        sys.exit(1)
    logger.info("使用 claude：%s，模型：%s，資料夾：%s", claude_path, config.claude_model, config.data_dir)

    config.photos_dir.mkdir(parents=True, exist_ok=True)
    processor = Processor(
        analyzer=Analyzer(claude_path=claude_path, model=config.claude_model, data_dir=config.data_dir),
        store=ExcelStore(config.excel_path),
        data_dir=config.data_dir,
        photos_dir=config.photos_dir,
    )
    client = MusicBotClient(processor, config.channel_id, config.state_path)

    try:
        client.run(config.discord_token, log_handler=None)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，已關閉")
    except Exception as e:
        explanation = explain_startup_error(e)
        if explanation is None:
            raise
        logger.error("%s", explanation)
        sys.exit(1)


if __name__ == "__main__":
    main()
