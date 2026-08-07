from __future__ import annotations

import os

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


def test_scalar_expense_categories_are_rejected(tmp_path):
    path = write_config(tmp_path, "line_bot:\n  expense_categories: food\n")

    with pytest.raises(ConfigError, match="expense_categories"):
        load_config(path, env={"VAULT_PATH": str(tmp_path / "vault")})


def test_empty_expense_categories_disable_the_question(tmp_path):
    config = load_config(
        write_config(tmp_path, "line_bot:\n  expense_categories: []\n"),
        env={"VAULT_PATH": str(tmp_path / "vault")},
    )

    assert config.line_bot.expense_categories == ()


# -- .env loading -------------------------------------------------------


def test_read_env_file_parses_the_shapes_people_write(tmp_path):
    from satemshi.config import read_env_file

    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        "VAULT_PATH=/srv/vault\n"
        "  SPACED = value with spaces \n"
        'QUOTED="quoted value"\n'
        "SINGLE='single'\n"
        "export EXPORTED=yes\n"
        "EMPTY=\n"
        "HASH_IN_VALUE=abc#def\n"
        "no_equals_sign\n",
        encoding="utf-8",
    )

    assert read_env_file(tmp_path / ".env") == {
        "VAULT_PATH": "/srv/vault",
        "SPACED": "value with spaces",
        "QUOTED": "quoted value",
        "SINGLE": "single",
        "EXPORTED": "yes",
        "EMPTY": "",
        # A "#" inside a value is part of the value — secrets may hold one.
        "HASH_IN_VALUE": "abc#def",
    }


def test_read_env_file_absent_is_empty(tmp_path):
    from satemshi.config import read_env_file

    assert read_env_file(tmp_path / "nope.env") == {}


def test_apply_env_file_sets_missing_keys(tmp_path, monkeypatch):
    from satemshi.config import apply_env_file

    (tmp_path / ".env").write_text("LINE_CHANNEL_SECRET=shh\n", encoding="utf-8")
    monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)

    assert apply_env_file()[1] == ["LINE_CHANNEL_SECRET"]
    assert os.environ["LINE_CHANNEL_SECRET"] == "shh"


def test_the_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    from satemshi.config import apply_env_file

    (tmp_path / ".env").write_text("VAULT_PATH=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("VAULT_PATH", "/from/shell")
    monkeypatch.chdir(tmp_path)

    assert apply_env_file()[1] == []
    assert os.environ["VAULT_PATH"] == "/from/shell"


def test_env_file_reaches_load_config(tmp_path, monkeypatch):
    """The bug this fixes: credentials in .env never reached the app."""
    from satemshi.config import apply_env_file

    (tmp_path / ".env").write_text(
        f"VAULT_PATH={tmp_path / 'vault'}\n"
        "LINE_CHANNEL_SECRET=secret-from-env-file\n"
        "LINE_CHANNEL_ACCESS_TOKEN=token-from-env-file\n",
        encoding="utf-8",
    )
    for key in ("VAULT_PATH", "LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    apply_env_file()
    config = load_config()

    assert config.vault_path == tmp_path / "vault"
    assert config.line_channel_secret == "secret-from-env-file"
    assert config.line_channel_access_token == "token-from-env-file"


def test_a_blank_shell_variable_does_not_shadow_the_file(tmp_path, monkeypatch):
    """Sourcing an unfilled .env exports blanks; the file must still win.

    This is the trap that cost a real debugging session: `set -a; source
    .env` on a template exports every key as "", and treating those as
    "already set" makes the app ignore credentials sitting in the file.
    """
    from satemshi.config import apply_env_file

    (tmp_path / ".env").write_text(
        "LINE_CHANNEL_SECRET=real-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "")
    monkeypatch.chdir(tmp_path)

    _, applied, shadowed = apply_env_file()

    assert applied == ["LINE_CHANNEL_SECRET"]
    assert shadowed == []
    assert os.environ["LINE_CHANNEL_SECRET"] == "real-secret"


def test_a_real_shell_variable_is_reported_as_shadowing(tmp_path, monkeypatch):
    from satemshi.config import apply_env_file

    (tmp_path / ".env").write_text("VAULT_PATH=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("VAULT_PATH", "/from/shell")
    monkeypatch.chdir(tmp_path)

    _, applied, shadowed = apply_env_file()

    assert applied == []
    assert shadowed == ["VAULT_PATH"]      # so the caller can say so
    assert os.environ["VAULT_PATH"] == "/from/shell"


def test_an_unfilled_template_applies_nothing(tmp_path, monkeypatch):
    from satemshi.config import apply_env_file

    (tmp_path / ".env").write_text(
        "VAULT_PATH=\nLINE_CHANNEL_SECRET=\n", encoding="utf-8"
    )
    for key in ("VAULT_PATH", "LINE_CHANNEL_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    path, applied, shadowed = apply_env_file()

    assert (applied, shadowed) == ([], [])
    assert path == tmp_path / ".env"       # so the caller can name the file


def test_missing_file_reports_the_path_it_looked_at(tmp_path, monkeypatch):
    from satemshi.config import apply_env_file

    monkeypatch.chdir(tmp_path)
    path, applied, shadowed = apply_env_file()

    assert (applied, shadowed) == ([], [])
    assert path == tmp_path / ".env" and not path.is_file()
