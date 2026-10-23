"""從 .env 讀設定（architecture.md 第 11 節）。

四個設定項目：DISCORD_TOKEN、CHANNEL_ID、CLAUDE_MODEL、DATA_DIR。
DISCORD_TOKEN 是密碼：不進 repr、不進錯誤訊息、不進 log。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

REQUIRED = ("DISCORD_TOKEN", "CHANNEL_ID", "CLAUDE_MODEL", "DATA_DIR")

# src/music_bot/config.py → 專案根目錄（.env 和相對的 DATA_DIR 都以它為準，不看從哪裡啟動）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """設定缺漏或格式不對。訊息只會寫欄位名稱，不會寫欄位的值。"""


@dataclass(frozen=True)
class Config:
    discord_token: str = field(repr=False)
    channel_id: int
    claude_model: str
    data_dir: Path

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def excel_path(self) -> Path:
        return self.data_dir / "music_records.xlsx"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def parse_config(values: Mapping[str, str | None], base_dir: Path) -> Config:
    cleaned = {name: (values.get(name) or "").strip() for name in REQUIRED}
    missing = [name for name in REQUIRED if not cleaned[name]]
    if missing:
        raise ConfigError(f".env 缺少設定：{', '.join(missing)}（參考 .env.example）")

    channel_text = cleaned["CHANNEL_ID"]
    if not channel_text.isascii() or not channel_text.isdigit() or int(channel_text) <= 0:
        raise ConfigError("CHANNEL_ID 必須是正整數（在 Discord 對頻道按右鍵 → 複製頻道 ID）")

    data_dir = Path(cleaned["DATA_DIR"])
    if not data_dir.is_absolute():
        data_dir = base_dir / data_dir

    return Config(
        discord_token=cleaned["DISCORD_TOKEN"],
        channel_id=int(channel_text),
        claude_model=cleaned["CLAUDE_MODEL"],
        data_dir=data_dir.resolve(),
    )


def load_config(env_path: Path | None = None) -> Config:
    """讀專案根目錄的 .env；系統環境變數若有同名設定，以環境變數為準。"""
    env_path = env_path or PROJECT_ROOT / ".env"
    values: dict[str, str | None] = dict(dotenv_values(env_path))
    for name in REQUIRED:
        if name in os.environ:
            values[name] = os.environ[name]
    return parse_config(values, base_dir=env_path.parent)
