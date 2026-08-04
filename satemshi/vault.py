"""Writing into the vault's RAW zone.

The vault is the database, and the user's freehand journal shares the
same file as our captures. So every write here obeys one rule: only the
bytes between the RAW markers may change. If the markers are missing we
append a fresh zone at the end of the note; we never reflow, reorder or
rewrite anything else in the file.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from .config import RawConfig
from .models import RawEntry


class VaultError(RuntimeError):
    """Raised when the vault or a daily note is not in a usable state."""


class VaultWriter:
    def __init__(self, vault_path: Path, raw: RawConfig) -> None:
        self.vault_path = Path(vault_path)
        self.raw = raw

    # -- paths ---------------------------------------------------------

    def daily_note_path(self, day: date) -> Path:
        return (
            self.vault_path
            / self.raw.daily_notes_dir
            / f"{day.isoformat()}.md"
        )

    # -- reading -------------------------------------------------------

    def read_raw_zone(self, day: date) -> str:
        """Return the RAW zone body for ``day`` ("" when there is none)."""
        path = self.daily_note_path(day)
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8")
        try:
            start, end = self._zone_bounds(text)
        except LookupError:
            return ""
        return text[start:end].strip("\n")

    def has_entry(self, day: date, anchor: str) -> bool:
        return f"^{anchor}" in self.read_raw_zone(day)

    # -- writing -------------------------------------------------------

    def append(self, entry: RawEntry, day: date | None = None) -> bool:
        """Append ``entry`` to the RAW zone.

        Returns ``False`` when an entry with the same anchor is already
        there — LINE redelivers webhook events, and a redelivery must not
        duplicate a capture.
        """
        day = day or entry.at.date()
        path = self.daily_note_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)

        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        try:
            start, end = self._zone_bounds(text)
        except LookupError:
            text = self._append_zone(text, day)
            start, end = self._zone_bounds(text)

        zone = text[start:end]
        if f"^{entry.anchor}" in zone:
            return False

        body = zone.strip("\n")
        block = entry.to_markdown()
        new_zone = f"\n{body}\n{block}\n" if body else f"\n{block}\n"
        self._write_atomic(path, text[:start] + new_zone + text[end:])
        return True

    # -- internals -----------------------------------------------------

    def _zone_bounds(self, text: str) -> tuple[int, int]:
        """Character bounds of the zone body, exclusive of the markers."""
        start_marker = self.raw.start_marker
        end_marker = self.raw.end_marker
        start = text.find(start_marker)
        end = text.find(end_marker)
        if start == -1 and end == -1:
            raise LookupError("no RAW zone")
        if start == -1 or end == -1 or end < start:
            raise VaultError(
                "RAW zone markers are unbalanced in this note; fix them by "
                f"hand before capturing again ({start_marker} / {end_marker})"
            )
        return start + len(start_marker), end

    def _append_zone(self, text: str, day: date) -> str:
        prefix = ""
        if text and not text.endswith("\n"):
            prefix = "\n"
        if text.strip():
            prefix += "\n"
        heading = f"{self.raw.heading}\n\n" if self.raw.heading else ""
        return (
            f"{text}{prefix}{heading}"
            f"{self.raw.start_marker}\n{self.raw.end_marker}\n"
        )

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
