"""Finding and storing the photos that belong to a given day.

Two sources feed the same list:

- images sent to the LINE bot, downloaded and stored in the vault;
- images already sitting in configured ``source_dirs`` (a phone sync
  folder, a camera import directory), filtered to the day in question.

"When was this taken" prefers EXIF ``DateTimeOriginal`` and falls back to
the file's modification time, so a screenshot or a PNG still lands on the
right day. EXIF parsing is done here rather than via Pillow to keep the
capture path dependency-free.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from pathlib import Path

from .config import PhotosConfig

# Enough to cover the APP1 segment, which is capped at 64 KiB and sits at
# the front of the file.
_EXIF_SCAN_BYTES = 128 * 1024

_TAG_DATETIME = 0x0132
_TAG_EXIF_IFD = 0x8769
_TAG_DATETIME_ORIGINAL = 0x9003

_CONTENT_TYPE_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/heic": ".heic",
    "image/heif": ".heic",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(frozen=True)
class Photo:
    """A photo on disk, with the moment it was taken."""

    path: Path
    taken_at: datetime
    source: str
    vault_relative: str | None = None

    @property
    def display(self) -> str:
        return self.vault_relative or self.path.name


# -- EXIF ---------------------------------------------------------------


def _read_ifd(tiff: bytes, order: str, offset: int) -> dict[int, tuple[int, int, int]]:
    """Map tag -> (type, count, value-or-offset) for one IFD."""
    if offset <= 0 or offset + 2 > len(tiff):
        return {}
    (count,) = struct.unpack_from(order + "H", tiff, offset)
    entries: dict[int, tuple[int, int, int]] = {}
    for index in range(count):
        entry = offset + 2 + index * 12
        if entry + 12 > len(tiff):
            break
        tag, kind, length = struct.unpack_from(order + "HHI", tiff, entry)
        (value,) = struct.unpack_from(order + "I", tiff, entry + 8)
        entries[tag] = (kind, length, value)
    return entries


def _ascii_value(tiff: bytes, order: str, entry: tuple[int, int, int]) -> str | None:
    kind, length, value = entry
    if kind != 2 or length == 0:
        return None
    if length <= 4:
        # The value is inlined in the 4 value bytes themselves.
        raw = struct.pack(order + "I", value)[:length]
    else:
        if value + length > len(tiff):
            return None
        raw = tiff[value : value + length]
    return raw.split(b"\x00", 1)[0].decode("ascii", "ignore").strip()


def _parse_tiff_datetime(tiff: bytes) -> datetime | None:
    if len(tiff) < 8:
        return None
    if tiff[:2] == b"II":
        order = "<"
    elif tiff[:2] == b"MM":
        order = ">"
    else:
        return None
    magic, ifd0_offset = struct.unpack_from(order + "HI", tiff, 2)
    if magic != 42:
        return None

    ifd0 = _read_ifd(tiff, order, ifd0_offset)
    candidates: list[str] = []
    if _TAG_EXIF_IFD in ifd0:
        exif_ifd = _read_ifd(tiff, order, ifd0[_TAG_EXIF_IFD][2])
        if _TAG_DATETIME_ORIGINAL in exif_ifd:
            text = _ascii_value(tiff, order, exif_ifd[_TAG_DATETIME_ORIGINAL])
            if text:
                candidates.append(text)
    if _TAG_DATETIME in ifd0:
        text = _ascii_value(tiff, order, ifd0[_TAG_DATETIME])
        if text:
            candidates.append(text)

    for text in candidates:
        try:
            return datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
    return None


def exif_datetime(data: bytes) -> datetime | None:
    """Naive capture time from a JPEG's EXIF, or ``None``."""
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xDA, 0xD9):  # start of scan / end of image
            return None
        (seglen,) = struct.unpack_from(">H", data, pos + 2)
        if seglen < 2:
            return None
        if marker == 0xE1 and data[pos + 4 : pos + 10] == b"Exif\x00\x00":
            return _parse_tiff_datetime(data[pos + 10 : pos + 2 + seglen])
        pos += 2 + seglen
    return None


def taken_at(path: Path, tz: tzinfo) -> datetime:
    """Capture time of ``path``, EXIF first, mtime as the fallback."""
    try:
        if path.suffix.lower() in (".jpg", ".jpeg"):
            with path.open("rb") as handle:
                naive = exif_datetime(handle.read(_EXIF_SCAN_BYTES))
            if naive is not None:
                return naive.replace(tzinfo=tz)
        return datetime.fromtimestamp(path.stat().st_mtime, tz)
    except OSError:
        return datetime.fromtimestamp(0, tz)


# -- store & find -------------------------------------------------------


def suffix_for_content_type(content_type: str | None) -> str:
    if not content_type:
        return ".jpg"
    return _CONTENT_TYPE_SUFFIX.get(content_type.split(";")[0].strip().lower(), ".jpg")


class PhotoStore:
    def __init__(self, vault_path: Path, config: PhotosConfig, tz: tzinfo) -> None:
        self.vault_path = Path(vault_path)
        self.config = config
        self.tz = tz

    def attachments_dir(self, when: datetime) -> Path:
        relative = self.config.attachments_dir.format(
            yyyy=f"{when:%Y}", mm=f"{when:%m}", dd=f"{when:%d}"
        )
        return self.vault_path / relative

    def store(self, data: bytes, when: datetime, name: str, suffix: str) -> Photo:
        """Write ``data`` into the vault and return it as a :class:`Photo`."""
        directory = self.attachments_dir(when)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{when:%Y%m%d-%H%M%S}-{name}{suffix}"
        if not path.exists():
            path.write_bytes(data)
        return Photo(
            path=path,
            taken_at=when,
            source="line",
            vault_relative=path.relative_to(self.vault_path).as_posix(),
        )

    def find_for_day(self, day: date) -> list[Photo]:
        """Every photo whose capture time falls on ``day``, deduplicated."""
        found: dict[Path, Photo] = {}
        for directory, source in self._search_roots(day):
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if not path.is_file() or not self.config.matches_extension(path.name):
                    continue
                resolved = path.resolve()
                if resolved in found:
                    continue
                when = taken_at(path, self.tz)
                if when.date() != day:
                    continue
                found[resolved] = Photo(
                    path=path,
                    taken_at=when,
                    source=source,
                    vault_relative=self._vault_relative(path),
                )
        return sorted(found.values(), key=lambda photo: photo.taken_at)

    def _search_roots(self, day: date) -> list[tuple[Path, str]]:
        midday = datetime(day.year, day.month, day.day, 12, tzinfo=self.tz)
        roots: list[tuple[Path, str]] = [(self.attachments_dir(midday), "vault")]
        # Resolved, so a photo outside the vault is recorded by an
        # absolute path rather than one relative to the working directory.
        roots.extend(
            (Path(directory).expanduser().resolve(), "scan")
            for directory in self.config.source_dirs
        )
        return roots

    def _vault_relative(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.vault_path.resolve()).as_posix()
        except ValueError:
            return None
