"""The two shapes anything writes into the vault.

A :class:`RawEntry` is a captured moment as data — one line, plus
``key:: value`` fields the nightly ingest reads without parsing prose. A
:class:`JournalEntry` is the opposite: the day as the user wrote it, kept
verbatim, for a person to read.
"""

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


def trim_lines(text: str) -> str:
    """Trailing whitespace off every line, blank lines off both ends.

    Prose keeps its shape — a message sent as three lines stays three
    lines — but the text must not *end* blank: a block id belongs to the
    paragraph it sits in, and a trailing empty line would hand it to a
    paragraph of its own.
    """
    lines = [line.rstrip() for line in str(text).splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


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


@dataclass
class JournalEntry:
    """One line of the day's journal, in the user's own words.

    Prose, not data: no kind, no fields, and newlines survive — the
    JOURNAL zone is written for a person to read, and the ingest quotes
    it rather than parsing it. It still carries a block id, which earns
    its keep twice: a redelivered LINE message finds its own id already
    in the note and writes nothing, and the wiki can embed the exact
    paragraph (``![[2026-08-04#^journal-abc]]``).
    """

    at: datetime
    text: str
    entry_id: str

    @property
    def anchor(self) -> str:
        return f"journal-{block_id(self.entry_id)}"

    def to_markdown(self, timestamps: bool = True) -> str:
        body = trim_lines(self.text) or "(empty)"
        stamp = f"**{self.at:%H:%M}** — " if timestamps else ""
        return f"{stamp}{body} ^{self.anchor}"
