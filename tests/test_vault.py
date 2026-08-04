from __future__ import annotations

from datetime import date, datetime

import pytest

from conftest import TZ
from satemshi.config import RawConfig
from satemshi.models import RawEntry
from satemshi.vault import VaultError, VaultWriter

DAY = date(2026, 8, 4)


def entry(entry_id: str = "abc", title: str = "Team lunch") -> RawEntry:
    return RawEntry(
        at=datetime(2026, 8, 4, 12, 5, tzinfo=TZ),
        kind="event",
        title=title,
        entry_id=entry_id,
        fields={"where": "Sizzler", "who": "Nok, Bank", "empty": ""},
    )


def writer(vault) -> VaultWriter:
    return VaultWriter(vault, RawConfig())


def test_creates_daily_note_with_zone(vault):
    assert writer(vault).append(entry()) is True

    text = (vault / "Daily Notes" / "2026-08-04.md").read_text()
    assert "## RAW" in text
    assert "<!-- raw:start -->" in text and "<!-- raw:end -->" in text
    assert "- **12:05** `event` Team lunch ^raw-abc" in text
    assert "    - where:: Sizzler" in text
    # Empty field values are dropped rather than written as bare keys.
    assert "empty::" not in text


def test_appends_without_touching_the_journal(vault):
    note = vault / "Daily Notes" / "2026-08-04.md"
    note.write_text(
        "# Wednesday\n\nMy own journal line.\n\n"
        "<!-- raw:start -->\n<!-- raw:end -->\n\n## Later thoughts\n\nkeep me\n",
        encoding="utf-8",
    )

    writer(vault).append(entry())

    text = note.read_text()
    assert text.startswith("# Wednesday\n\nMy own journal line.\n")
    assert text.endswith("## Later thoughts\n\nkeep me\n")
    body = text.split("<!-- raw:start -->")[1].split("<!-- raw:end -->")[0]
    assert "Team lunch" in body


def test_second_append_keeps_the_first(vault):
    writer(vault).append(entry("one", "First"))
    writer(vault).append(entry("two", "Second"))

    body = writer(vault).read_raw_zone(DAY)
    assert body.index("First") < body.index("Second")


def test_redelivered_event_is_not_duplicated(vault):
    assert writer(vault).append(entry("dup")) is True
    assert writer(vault).append(entry("dup")) is False

    assert writer(vault).read_raw_zone(DAY).count("^raw-dup") == 1


def test_unbalanced_markers_refuse_to_write(vault):
    note = vault / "Daily Notes" / "2026-08-04.md"
    note.write_text("<!-- raw:start -->\nhalf a zone\n", encoding="utf-8")

    with pytest.raises(VaultError):
        writer(vault).append(entry())


def test_read_raw_zone_is_empty_for_a_missing_note(vault):
    assert writer(vault).read_raw_zone(date(2020, 1, 1)) == ""
