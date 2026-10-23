"""__main__.py：啟動前的檢查與錯誤說明。"""

import logging

import discord
import pytest

from music_bot.__main__ import environment_warnings, explain_startup_error, setup_logging


def test_no_warnings_in_a_clean_environment():
    assert environment_warnings({}) == []


def test_api_key_in_environment_is_warned_without_leaking_it():
    warnings = environment_warnings({"ANTHROPIC_API_KEY": "sk-ant-very-secret"})
    assert len(warnings) == 1
    assert "ANTHROPIC_API_KEY" in warnings[0]
    assert "sk-ant-very-secret" not in warnings[0]


def test_empty_api_key_is_not_a_warning():
    assert environment_warnings({"ANTHROPIC_API_KEY": ""}) == []


def test_login_failure_points_at_the_token():
    text = explain_startup_error(discord.LoginFailure("Improper token has been passed."))
    assert "DISCORD_TOKEN" in text


def test_missing_privileged_intent_points_at_message_content_intent():
    text = explain_startup_error(discord.PrivilegedIntentsRequired(None))
    assert "Message Content Intent" in text
    assert "discord-bot-setup.md" in text


def test_other_errors_have_no_special_explanation():
    assert explain_startup_error(RuntimeError("x")) is None


@pytest.fixture
def clean_root_logger():
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    yield root
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)


def test_setup_logging_writes_to_a_file_under_logs_dir(tmp_path, clean_root_logger):
    setup_logging(tmp_path / "logs")
    logging.getLogger("music_bot.test").info("測試訊息 123")
    for h in clean_root_logger.handlers:
        h.flush()
    text = (tmp_path / "logs" / "agent.log").read_text(encoding="utf-8")
    assert "測試訊息 123" in text
