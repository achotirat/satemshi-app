from __future__ import annotations

import pytest

from satemshi.config import ConfigError, load_config

CONFIG = """
timezone: Europe/Berlin
raw:
  heading: "## Captured"
line_bot:
  webhook_path: /hooks/line
  allowed_user_ids: ["U1", "U2"]
  event_slots:
    - key: mood
      question: "How did it feel?"
photos:
  source_dirs: ["~/phone-sync"]
  extensions: [".JPG", ".png"]
"""


def write_config(tmp_path, text: str = CONFIG):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_yaml_and_env(tmp_path):
    config = load_config(
        write_config(tmp_path),
        env={
            "VAULT_PATH": str(tmp_path / "vault"),
            "LINE_CHANNEL_SECRET": "shh",
            "LINE_CHANNEL_ACCESS_TOKEN": "tok",
        },
    )

    assert config.vault_path == tmp_path / "vault"
    assert config.timezone == "Europe/Berlin"
    assert config.tzinfo.key == "Europe/Berlin"
    assert config.raw.heading == "## Captured"
    assert config.raw.start_marker == "<!-- raw:start -->"  # default kept
    assert config.line_bot.webhook_path == "/hooks/line"
    assert [slot.key for slot in config.line_bot.event_slots] == ["mood"]
    assert config.line_channel_secret == "shh"
    assert config.line_channel_access_token == "tok"


def test_extensions_are_normalised_to_lowercase(tmp_path):
    config = load_config(
        write_config(tmp_path), env={"VAULT_PATH": str(tmp_path / "vault")}
    )

    assert config.photos.extensions == (".jpg", ".png")
    assert config.photos.matches_extension("IMG_1.JPG") is True
    assert config.photos.matches_extension("clip.mov") is False


def test_allowlist_gates_senders(tmp_path):
    config = load_config(
        write_config(tmp_path), env={"VAULT_PATH": str(tmp_path / "vault")}
    )

    assert config.line_bot.is_allowed("U1") is True
    assert config.line_bot.is_allowed("U9") is False
    assert config.line_bot.is_allowed(None) is False


def test_an_empty_allowlist_allows_everyone(tmp_path):
    config = load_config(
        write_config(tmp_path, "line_bot:\n  allowed_user_ids: []\n"),
        env={"VAULT_PATH": str(tmp_path / "vault")},
    )

    assert config.line_bot.is_allowed("anyone") is True


def test_missing_vault_path_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="VAULT_PATH"):
        load_config(write_config(tmp_path), env={})


def test_malformed_event_slots_are_rejected(tmp_path):
    path = write_config(tmp_path, "line_bot:\n  event_slots:\n    - ask me things\n")

    with pytest.raises(ConfigError, match="event_slots"):
        load_config(path, env={"VAULT_PATH": str(tmp_path / "vault")})


def test_defaults_apply_without_a_config_file(tmp_path):
    config = load_config(
        tmp_path / "absent.yaml", env={"VAULT_PATH": str(tmp_path / "vault")}
    )

    assert config.timezone == "Asia/Bangkok"
    assert config.line_bot.webhook_path == "/line/webhook"
    assert [slot.key for slot in config.line_bot.event_slots] == [
        "when",
        "where",
        "who",
        "notes",
    ]
