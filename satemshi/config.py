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

DEFAULT_PHOTO_SLOTS: list[dict[str, str]] = [
    {"key": "where", "question": "Where was this taken?"},
    {"key": "what", "question": "What is it — and who's in it?"},
]

DEFAULT_EXPENSE_CATEGORIES: tuple[str, ...] = (
    "food",
    "transport",
    "groceries",
    "household",
    "health",
    "fun",
    "bills",
    "other",
)


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
    photo_slots: tuple[EventSlot, ...] = ()
    expense_categories: tuple[str, ...] = DEFAULT_EXPENSE_CATEGORIES

    def is_allowed(self, user_id: str | None) -> bool:
        """An empty allowlist means "anyone who can reach the webhook"."""
        if not self.allowed_user_ids:
            return True
        return user_id is not None and user_id in self.allowed_user_ids


@dataclass(frozen=True)
class VaultGitConfig:
    """Auto-commit of the vault repo after a capture.

    Off unless asked for: the vault is the user's own git repo, and
    committing into it on their behalf is not something to start doing
    because a config key was absent. ``config.example.yaml`` turns it on.
    """

    enabled: bool = False
    # Wait this long after the first change before committing, so a
    # capture and the answers that follow it land in one commit rather
    # than five. Bounds the delay too — every change is committed within
    # this window of arriving, however many more follow.
    coalesce_seconds: int = 300
    auto_push: bool = False


@dataclass(frozen=True)
class Config:
    vault_path: Path
    timezone: str = DEFAULT_TIMEZONE
    raw: RawConfig = field(default_factory=RawConfig)
    photos: PhotosConfig = field(default_factory=PhotosConfig)
    line_bot: LineBotConfig = field(default_factory=LineBotConfig)
    vault_git: VaultGitConfig = field(default_factory=VaultGitConfig)
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


def _slots(
    raw_slots, defaults: list[dict[str, str]], name: str
) -> tuple[EventSlot, ...]:
    slots = raw_slots if raw_slots is not None else defaults
    parsed: list[EventSlot] = []
    for entry in slots:
        if not isinstance(entry, dict) or "key" not in entry or "question" not in entry:
            raise ConfigError(
                f"line_bot.{name} entries need both a 'key' and a 'question'"
            )
        parsed.append(EventSlot(key=str(entry["key"]), question=str(entry["question"])))
    return tuple(parsed)


def _categories(raw) -> tuple[str, ...]:
    """An explicit empty list disables the category question."""
    if raw is None:
        return DEFAULT_EXPENSE_CATEGORIES
    if isinstance(raw, str) or not isinstance(raw, list | tuple):
        # A bare string would be iterated character by character.
        raise ConfigError(
            "line_bot.expense_categories must be a list of names, e.g. "
            "[food, transport]"
        )
    return tuple(str(category) for category in raw)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a plain dict.

    Deliberately small: ``KEY=value`` a line, ``#`` comments and blank
    lines skipped, an optional ``export`` prefix tolerated, and matching
    surrounding quotes stripped. Values are taken verbatim otherwise —
    a ``#`` inside a value is part of the value, not a comment, because
    secrets are allowed to contain one.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def apply_env_file(path: Path | None = None) -> tuple[Path, list[str], list[str]]:
    """Load ``.env`` into the process environment.

    Returns ``(path_searched, keys_applied, keys_shadowed)`` — enough for
    the caller to say what happened, including when nothing happened.

    A real environment variable wins over the file, so ``VAULT_PATH=/tmp/v
    python -m satemshi`` still overrides it. But an *empty* one does not
    count as set: the usual way a variable ends up empty in a shell is
    sourcing a template that was never filled in, and honouring that
    over the real file means the app ignores credentials that are
    sitting right there.
    """
    path = Path.cwd() / ".env" if path is None else path
    applied: list[str] = []
    shadowed: list[str] = []
    for key, value in read_env_file(path).items():
        if os.environ.get(key):
            if value:
                shadowed.append(key)
            continue
        os.environ[key] = value
        if value:
            applied.append(key)
    return path, applied, shadowed


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
        event_slots=_slots(
            bot_data.get("event_slots"), DEFAULT_EVENT_SLOTS, "event_slots"
        ),
        photo_slots=_slots(
            bot_data.get("photo_slots"), DEFAULT_PHOTO_SLOTS, "photo_slots"
        ),
        expense_categories=_categories(bot_data.get("expense_categories")),
    )

    git_data = data.get("vault_git") or {}
    vault_git = VaultGitConfig(
        enabled=bool(git_data.get("enabled", VaultGitConfig.enabled)),
        # A negative window would mean "commit before the write finishes".
        coalesce_seconds=max(
            0,
            int(git_data.get("coalesce_seconds", VaultGitConfig.coalesce_seconds)),
        ),
        auto_push=bool(git_data.get("auto_push", VaultGitConfig.auto_push)),
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
        vault_git=vault_git,
        line_channel_secret=env.get("LINE_CHANNEL_SECRET", ""),
        line_channel_access_token=env.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
    )
