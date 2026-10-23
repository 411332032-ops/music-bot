"""config.py：從 .env 讀設定（architecture.md 第 11 節）。"""

from pathlib import Path

import pytest

from music_bot.config import Config, ConfigError, parse_config

GOOD = {
    "DISCORD_TOKEN": "super-secret-token-value",
    "CHANNEL_ID": "1562013219382624276",
    "CLAUDE_MODEL": "haiku",
    "DATA_DIR": "./data",
}


def test_all_four_settings_are_read(tmp_path):
    cfg = parse_config(GOOD, base_dir=tmp_path)
    assert cfg.discord_token == "super-secret-token-value"
    assert cfg.channel_id == 1562013219382624276
    assert cfg.claude_model == "haiku"
    assert isinstance(cfg, Config)


def test_relative_data_dir_is_resolved_against_base_dir_not_cwd(tmp_path):
    cfg = parse_config(GOOD, base_dir=tmp_path)
    assert cfg.data_dir == (tmp_path / "data").resolve()


def test_absolute_data_dir_is_kept(tmp_path):
    absolute = tmp_path / "elsewhere"
    cfg = parse_config({**GOOD, "DATA_DIR": str(absolute)}, base_dir=Path("C:/ignored"))
    assert cfg.data_dir == absolute.resolve()


def test_derived_paths_live_under_data_dir(tmp_path):
    cfg = parse_config(GOOD, base_dir=tmp_path)
    assert cfg.photos_dir == cfg.data_dir / "photos"
    assert cfg.excel_path == cfg.data_dir / "music_records.xlsx"
    assert cfg.state_path == cfg.data_dir / "state.json"
    assert cfg.logs_dir == cfg.data_dir / "logs"


@pytest.mark.parametrize("name", list(GOOD))
def test_missing_setting_is_named_in_the_error(tmp_path, name):
    values = {k: v for k, v in GOOD.items() if k != name}
    with pytest.raises(ConfigError) as e:
        parse_config(values, base_dir=tmp_path)
    assert name in str(e.value)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_setting_counts_as_missing(tmp_path, blank):
    with pytest.raises(ConfigError) as e:
        parse_config({**GOOD, "CLAUDE_MODEL": blank}, base_dir=tmp_path)
    assert "CLAUDE_MODEL" in str(e.value)


def test_all_missing_names_are_listed_together(tmp_path):
    with pytest.raises(ConfigError) as e:
        parse_config({}, base_dir=tmp_path)
    for name in GOOD:
        assert name in str(e.value)


@pytest.mark.parametrize("bad", ["abc", "12.5", "-3", "0"])
def test_channel_id_must_be_a_positive_integer(tmp_path, bad):
    with pytest.raises(ConfigError) as e:
        parse_config({**GOOD, "CHANNEL_ID": bad}, base_dir=tmp_path)
    assert "CHANNEL_ID" in str(e.value)


def test_values_are_stripped(tmp_path):
    cfg = parse_config({**GOOD, "CLAUDE_MODEL": "  haiku  ", "CHANNEL_ID": " 123 "}, base_dir=tmp_path)
    assert cfg.claude_model == "haiku"
    assert cfg.channel_id == 123


def test_token_never_appears_in_repr_or_errors(tmp_path):
    cfg = parse_config(GOOD, base_dir=tmp_path)
    assert GOOD["DISCORD_TOKEN"] not in repr(cfg)
    assert GOOD["DISCORD_TOKEN"] not in str(cfg)
    with pytest.raises(ConfigError) as e:
        parse_config({**GOOD, "CHANNEL_ID": "abc"}, base_dir=tmp_path)
    assert GOOD["DISCORD_TOKEN"] not in str(e.value)
