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
        title = self.title.strip() or "(no title)"
        head = f"- **{self.at:%H:%M}** `{self.kind}` {title} ^{self.anchor}"
        lines = [head]
        for key, value in self.fields.items():
            value = str(value).strip()
            if not value:
                continue
            # Keep multi-line values inside the list item so the bullet
            # structure — and therefore the Dataview field — survives.
            value = value.replace("\n", "\n      ")
            lines.append(f"    - {key}:: {value}")
        return "\n".join(lines)
