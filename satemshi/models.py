"""The one shape everything writes into the vault: a RAW entry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_BLOCK_ID_SAFE = re.compile(r"[^A-Za-z0-9-]")


def block_id(source: str) -> str:
    """Obsidian block identifiers allow only letters, digits and dashes."""
    cleaned = _BLOCK_ID_SAFE.sub("-", source).strip("-")
    return cleaned or "entry"


def flatten_text(text: str) -> str:
    """Collapse user text onto one line.

    The RAW zone is line-oriented — one line per entry, one per field —
    and everything here is user-controlled chat text. A value carrying a
    newline would otherwise start a new column-0 line, which the zone
    parser would read as a (possibly forged) entry of its own.
    """
    parts = (part.strip() for part in str(text).splitlines())
    return " / ".join(part for part in parts if part)


@dataclass
class RawEntry:
    """A single captured moment, rendered verbatim into the RAW zone.

    Fields are Dataview inline fields (``key:: value``) so the nightly
    ingest can read them back without parsing prose.
    """

    at: datetime
    kind: str
    title: str
    entry_id: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def anchor(self) -> str:
        return f"raw-{block_id(self.entry_id)}"

    def to_markdown(self) -> str:
        title = flatten_text(self.title) or "(no title)"
        head = f"- **{self.at:%H:%M}** `{self.kind}` {title} ^{self.anchor}"
        lines = [head]
        for key, value in self.fields.items():
            value = flatten_text(value)
            if not value:
                continue
            lines.append(f"    - {key}:: {value}")
        return "\n".join(lines)
