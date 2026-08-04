from __future__ import annotations

import asyncio
import struct
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from satemshi.config import Config, EventSlot, LineBotConfig, PhotosConfig, RawConfig

TZ = ZoneInfo("Asia/Bangkok")


def run(coro):
    """Drive a coroutine from a synchronous test."""
    return asyncio.run(coro)


class FakeClient:
    """Stands in for the LINE Messaging API."""

    def __init__(self, content: bytes = b"", content_type: str = "image/jpeg") -> None:
        self.replies: list[tuple[str, str]] = []
        self.pushes: list[tuple[str, str]] = []
        self.content = content
        self.content_type = content_type
        self.fail_content = False

    async def reply(self, reply_token: str, text: str) -> None:
        self.replies.append((reply_token, text))

    async def push(self, to: str, text: str) -> None:
        self.pushes.append((to, text))

    async def get_content(self, message_id: str) -> tuple[bytes, str]:
        if self.fail_content:
            raise RuntimeError("download failed")
        return self.content, self.content_type

    @property
    def last_reply(self) -> str:
        return self.replies[-1][1] if self.replies else ""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault"
    (path / "Daily Notes").mkdir(parents=True)
    return path


@pytest.fixture
def session_dir(tmp_path: Path) -> str:
    """Keep conversation state out of the working directory during tests."""
    return str(tmp_path / "sessions")


@pytest.fixture
def make_config(vault: Path, session_dir: str):
    def _make(**overrides) -> Config:
        defaults = dict(
            vault_path=vault,
            timezone="Asia/Bangkok",
            raw=RawConfig(),
            photos=PhotosConfig(),
            line_bot=LineBotConfig(
                session_dir=session_dir,
                event_slots=(
                    EventSlot("when", "When did it happen?"),
                    EventSlot("who", "Who was there?"),
                ),
            ),
            line_channel_secret="s3cret",
            line_channel_access_token="token",
        )
        defaults.update(overrides)
        return Config(**defaults)

    return _make


@pytest.fixture
def now():
    return lambda: datetime(2026, 8, 4, 14, 30, tzinfo=TZ)


def jpeg_with_exif(taken: str = "2026:08:04 09:15:00") -> bytes:
    """A minimal JPEG carrying one EXIF DateTimeOriginal tag."""
    stamp = taken.encode("ascii") + b"\x00"
    ifd0_offset = 8
    exif_ifd_offset = ifd0_offset + 2 + 12 + 4
    data_offset = exif_ifd_offset + 2 + 12 + 4

    ifd0 = (
        struct.pack("<H", 1)
        + struct.pack("<HHII", 0x8769, 4, 1, exif_ifd_offset)
        + struct.pack("<I", 0)
    )
    exif_ifd = (
        struct.pack("<H", 1)
        + struct.pack("<HHII", 0x9003, 2, len(stamp), data_offset)
        + struct.pack("<I", 0)
    )
    tiff = b"II" + struct.pack("<HI", 42, ifd0_offset) + ifd0 + exif_ifd + stamp

    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + app1 + b"\xff\xd9"
