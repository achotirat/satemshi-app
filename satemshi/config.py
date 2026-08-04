"""Instance configuration loading.

Config comes from two places, and the split is deliberate:

- ``config.yaml`` (or ``$SATEMSHI_CONFIG``) holds behaviour — markers,
  slot questions, photo directories. It is gitignored but not secret.
- ``.env`` / the process environment holds secrets and machine-specific
  paths — the vault location and the LINE channel credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DEFAULT_TIMEZONE = "Asia/Bangkok"

DEFAULT_EVENT_SLOTS: list[dict[str, str]] = [
    {"key": "when", "question": "When did it happen? (e.g. 14:30, morning, all day)"},
    {"key": "where", "question": "Where was it?"},
    {"key": "who", "question": "Who was there? (comma-separated)"},
    {"key": "notes", "question": "Anything else worth remembering?"},
]


class ConfigError(RuntimeError):
    """Raised when configuration is missing or unusable."""


@dataclass(frozen=True)
class RawConfig:
    """The RAW zone of a daily note — where captures land verbatim."""

    start_marker: str = "<!-- raw:start -->"
    end_marker: str = "<!-- raw:end -->"
    heading: str = "## RAW"
    daily_notes_dir: str = "Daily Notes"


@dataclass(frozen=True)
class PhotosConfig:
    """Where photos are stored, and where to look for today's ones."""

    attachments_dir: str = "Attachments/{yyyy}/{mm}"
    source_dirs: tuple[str, ...] = ()
    extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".heic", ".webp")

    def matches_extension(self, name: str) -> bool:
        return Path(name).suffix.lower() in self.extensions


@dataclass(frozen=True)
class EventSlot:
    key: str
    question: str


@dataclass(frozen=True)
class LineBotConfig:
    enabled: bool = True
    webhook_path: str = "/line/webhook"
    allowed_user_ids: tuple[str, ...] = ()
    session_dir: str = "sessions"
    session_ttl_seconds: int = 1800
    event_slots: tuple[EventSlot, ...] = ()

    def is_allowed(self, user_id: str | None) -> bool:
        """An empty allowlist means "anyone who can reach the webhook"."""
        if not self.allowed_user_ids:
            return True
        return user_id is not None and user_id in self.allowed_user_ids


@dataclass(frozen=True)
class Config:
    vault_path: Path
    timezone: str = DEFAULT_TIMEZONE
    raw: RawConfig = field(default_factory=RawConfig)
    photos: PhotosConfig = field(default_factory=PhotosConfig)
    line_bot: LineBotConfig = field(default_factory=LineBotConfig)
    line_channel_secret: str = ""
    line_channel_access_token: str = ""

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:  # pragma: no cover - env specific
            raise ConfigError(f"Unknown timezone {self.timezone!r}") from exc


def _load_yaml(path: Path | None) -> dict:
    if path is None:
        return {}
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    from_env = os.environ.get("SATEMSHI_CONFIG")
    if from_env:
        return Path(from_env)
    default = Path.cwd() / "config.yaml"
    return default if default.is_file() else None


def _slots(raw_slots) -> tuple[EventSlot, ...]:
    slots = raw_slots if raw_slots is not None else DEFAULT_EVENT_SLOTS
    parsed: list[EventSlot] = []
    for entry in slots:
        if not isinstance(entry, dict) or "key" not in entry or "question" not in entry:
            raise ConfigError(
                "line_bot.event_slots entries need both a 'key' and a 'question'"
            )
        parsed.append(EventSlot(key=str(entry["key"]), question=str(entry["question"])))
    return tuple(parsed)


def load_config(
    path: Path | None = None, env: dict[str, str] | None = None
) -> Config:
    """Build a :class:`Config` from YAML plus environment overrides."""
    env = dict(os.environ if env is None else env)
    data = _load_yaml(_config_path(path))

    vault_raw = env.get("VAULT_PATH") or data.get("vault_path")
    if not vault_raw:
        raise ConfigError(
            "VAULT_PATH is not set. Copy .env.example to .env and point it at "
            "your Obsidian vault."
        )

    raw_data = data.get("raw") or {}
    photos_data = data.get("photos") or {}
    bot_data = data.get("line_bot") or {}

    extensions = photos_data.get("extensions")
    photos = PhotosConfig(
        attachments_dir=photos_data.get(
            "attachments_dir", PhotosConfig.attachments_dir
        ),
        source_dirs=tuple(photos_data.get("source_dirs") or ()),
        extensions=tuple(
            sorted({str(ext).lower() for ext in extensions})
            if extensions
            else PhotosConfig.extensions
        ),
    )

    line_bot = LineBotConfig(
        enabled=bool(bot_data.get("enabled", True)),
        webhook_path=bot_data.get("webhook_path", LineBotConfig.webhook_path),
        allowed_user_ids=tuple(bot_data.get("allowed_user_ids") or ()),
        session_dir=bot_data.get("session_dir", LineBotConfig.session_dir),
        session_ttl_seconds=int(
            bot_data.get("session_ttl_seconds", LineBotConfig.session_ttl_seconds)
        ),
        event_slots=_slots(bot_data.get("event_slots")),
    )

    return Config(
        vault_path=Path(vault_raw).expanduser(),
        timezone=data.get("timezone", DEFAULT_TIMEZONE),
        raw=RawConfig(
            start_marker=raw_data.get("start_marker", RawConfig.start_marker),
            end_marker=raw_data.get("end_marker", RawConfig.end_marker),
            heading=raw_data.get("heading", RawConfig.heading),
            daily_notes_dir=raw_data.get(
                "daily_notes_dir", RawConfig.daily_notes_dir
            ),
        ),
        photos=photos,
        line_bot=line_bot,
        line_channel_secret=env.get("LINE_CHANNEL_SECRET", ""),
        line_channel_access_token=env.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
    )
