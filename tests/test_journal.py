"""Writing the daily note itself — the JOURNAL zone and the bot's mode."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from test_handlers import DAY, image_event, make_bot, send, text_event

from conftest import TZ, FakeClient, jpeg_with_exif
from satemshi.config import ConfigError, JournalConfig, RawConfig, load_config
from satemshi.models import JournalEntry, RawEntry, trim_lines
from satemshi.vault import VaultError, VaultWriter


def line(text: str = "Slept badly, up before the heat.", entry_id: str = "j1"):
    return JournalEntry(
        at=datetime(2026, 8, 4, 6, 40, tzinfo=TZ), text=text, entry_id=entry_id
    )


def writer(vault, journal: JournalConfig | None = None) -> VaultWriter:
    return VaultWriter(vault, RawConfig(), journal=journal)


def note(vault) -> str:
    return (vault / "Daily Notes" / "2026-08-04.md").read_text(encoding="utf-8")


# -- the zone -----------------------------------------------------------


def test_creates_its_own_zone_beside_raw(vault):
    assert writer(vault).append_journal(line()) is True

    text = note(vault)
    assert "## Journal" in text
    assert "<!-- journal:start -->" in text and "<!-- journal:end -->" in text
    assert "**06:40** — Slept badly, up before the heat. ^journal-j1" in text
    # The journal is not a capture: no RAW zone is opened for it.
    assert "<!-- raw:start -->" not in text


def test_the_two_zones_stay_out_of_each_others_way(vault):
    write = writer(vault)
    write.append(
        RawEntry(
            at=datetime(2026, 8, 4, 12, 5, tzinfo=TZ),
            kind="event",
            title="Team lunch",
            entry_id="abc",
        )
    )
    write.append_journal(line())

    assert "Team lunch" in write.read_raw_zone(DAY)
    assert "Team lunch" not in write.read_journal(DAY)
    assert "Slept badly" in write.read_journal(DAY)
    assert "Slept badly" not in write.read_raw_zone(DAY)


def test_the_users_own_writing_is_untouched(vault):
    path = vault / "Daily Notes" / "2026-08-04.md"
    path.write_text(
        "# Tuesday\n\nMy own opening line.\n\n"
        "## Journal\n\n<!-- journal:start -->\n<!-- journal:end -->\n\n"
        "## Evening\n\nkeep me\n",
        encoding="utf-8",
    )

    writer(vault).append_journal(line())

    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Tuesday\n\nMy own opening line.\n")
    assert text.endswith("## Evening\n\nkeep me\n")
    assert "Slept badly" in writer(vault).read_journal(DAY)


def test_lines_are_separate_paragraphs_in_order(vault):
    write = writer(vault)
    write.append_journal(line("First thing.", "j1"))
    write.append_journal(line("Second thing.", "j2"))

    body = write.read_journal(DAY)
    assert body.index("First thing.") < body.index("Second thing.")
    # A blank line between them, so each ^id belongs to its own block.
    assert "^journal-j1\n\n" in body


def test_a_redelivered_message_is_written_once(vault):
    assert writer(vault).append_journal(line(entry_id="dup")) is True
    assert writer(vault).append_journal(line(entry_id="dup")) is False

    assert writer(vault).read_journal(DAY).count("^journal-dup") == 1


def test_newlines_in_a_message_survive(vault):
    writer(vault).append_journal(line("Morning: swim.\nEvening: nothing at all."))

    body = writer(vault).read_journal(DAY)
    assert "Morning: swim.\nEvening: nothing at all. ^journal-j1" in body


def test_trailing_blank_lines_do_not_orphan_the_block_id(vault):
    writer(vault).append_journal(line("A short day.  \n\n  \n"))

    assert "A short day. ^journal-j1" in writer(vault).read_journal(DAY)


def test_timestamps_can_be_turned_off(vault):
    writer(vault, JournalConfig(timestamps=False)).append_journal(line("Just prose."))

    body = writer(vault, JournalConfig(timestamps=False)).read_journal(DAY)
    assert body.startswith("Just prose. ^journal-j1")
    assert "06:40" not in body


def test_a_marker_inside_a_message_cannot_close_the_zone(vault):
    write = writer(vault)
    write.append_journal(line("Tried typing <!-- journal:end --> to see what happens"))
    write.append_journal(line("and the next line still lands inside.", "j2"))

    text = note(vault)
    assert text.count("<!-- journal:end -->") == 1
    body = write.read_journal(DAY)
    assert "still lands inside" in body
    # Broken by a zero-width space, so the line still reads as typed.
    assert "journal:end" in body


def test_a_marker_inside_a_capture_cannot_close_the_raw_zone(vault):
    write = writer(vault)
    write.append(
        RawEntry(
            at=datetime(2026, 8, 4, 9, 0, tzinfo=TZ),
            kind="note",
            title="mind the <!-- raw:end --> in this one",
            entry_id="a",
        )
    )
    write.append(
        RawEntry(
            at=datetime(2026, 8, 4, 9, 5, tzinfo=TZ),
            kind="note",
            title="second",
            entry_id="b",
        )
    )

    assert note(vault).count("<!-- raw:end -->") == 1
    assert "second" in write.read_raw_zone(DAY)


def test_unbalanced_journal_markers_refuse_to_write(vault):
    path = vault / "Daily Notes" / "2026-08-04.md"
    path.write_text("<!-- journal:start -->\nhalf a zone\n", encoding="utf-8")

    with pytest.raises(VaultError) as excinfo:
        writer(vault).append_journal(line())
    assert "JOURNAL" in str(excinfo.value)


def test_read_journal_is_empty_for_a_missing_note(vault):
    assert writer(vault).read_journal(date(2020, 1, 1)) == ""


def test_trim_lines_keeps_the_shape_of_prose():
    assert trim_lines("  one  \n\ntwo\n\n") == "  one\n\ntwo"
    assert trim_lines("\n\nonly\n") == "only"
    assert trim_lines("   ") == ""


# -- the bot -----------------------------------------------------------


def test_journal_with_text_writes_one_line(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal Quiet day, mostly reading.", "e1"))

    assert "Quiet day, mostly reading. ^journal-e1" in bot.vault.read_journal(DAY)
    assert "→ JOURNAL" in client.last_reply
    # No follow-up questions, and nothing in the capture zone.
    assert bot.vault.read_raw_zone(DAY) == ""


def test_a_thai_alias_works_too(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("บันทึก วันนี้ร้อนมาก", "e1"))

    assert "วันนี้ร้อนมาก" in bot.vault.read_journal(DAY)


def test_journal_mode_takes_every_message_until_done(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    assert "Journal open for 2026-08-04.md" in client.last_reply

    send(bot, text_event("Up at six, swam before breakfast.", "e2"))
    assert "1 line(s) today" in client.last_reply
    send(bot, text_event("Long call — they can deliver on Friday.", "e3"))
    assert "2 line(s) today" in client.last_reply

    send(bot, text_event("done", "e4"))
    assert "Journal closed — 2 line(s)" in client.last_reply

    body = bot.vault.read_journal(DAY)
    assert "Up at six" in body and "Long call" in body
    # Written as prose, not turned into an event capture with questions.
    assert bot.vault.read_raw_zone(DAY) == ""


def test_journal_mode_writes_each_line_as_it_arrives(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    send(bot, text_event("Halfway through the day.", "e2"))

    # Not held in the session until "done" — a capture that reached the
    # vault is not lost if the conversation is never closed.
    assert "Halfway through the day." in bot.vault.read_journal(DAY)


def test_a_command_word_in_journal_mode_is_journalled(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    send(bot, text_event("photos of the boat trip were the best part", "e2"))

    assert "photos of the boat trip" in bot.vault.read_journal(DAY)
    assert "line(s) today" in client.last_reply


def test_a_slashed_command_runs_without_closing_the_journal(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    send(bot, text_event("A good morning.", "e2"))
    send(bot, text_event("/today", "e3"))
    assert "Journal — 1 line(s)" in client.last_reply
    # The block ids are machinery; the readback is prose.
    assert "^journal-" not in client.last_reply

    send(bot, text_event("Still journalling.", "e4"))
    assert "2 line(s) today" in client.last_reply


def test_cancel_does_not_claim_the_lines_were_dropped(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    send(bot, text_event("Something worth keeping.", "e2"))
    send(bot, text_event("cancel", "e3"))

    assert "Nothing was written" not in client.last_reply
    assert "already in it" in client.last_reply
    assert "Something worth keeping." in bot.vault.read_journal(DAY)


def test_journal_mode_survives_a_redelivered_message(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    send(bot, text_event("Sent twice.", "e2"))
    send(bot, text_event("Sent twice.", "e2"))

    assert bot.vault.read_journal(DAY).count("Sent twice.") == 1


def test_journal_refuses_to_clobber_an_open_capture(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("Team lunch", "e1"))  # event capture opens
    send(bot, text_event("/journal", "e2"))
    assert "mid-capture" in client.last_reply

    send(bot, text_event("12:30", "e3"))  # the capture is still there
    assert "Who was there?" in client.last_reply


def test_one_line_mid_capture_leaves_the_capture_alone(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("Team lunch", "e1"))  # event capture opens
    send(bot, text_event("/journal Writing this down before I forget.", "e2"))
    assert "Writing this down" in bot.vault.read_journal(DAY)

    send(bot, text_event("12:30", "e3"))  # the capture is where we left it
    assert "Who was there?" in client.last_reply


def test_spent_in_journal_mode_says_the_journal_is_open(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    send(bot, text_event("/spent 120 lunch", "e2"))

    assert "The journal is open" in client.last_reply
    assert bot.vault.read_raw_zone(DAY) == ""


def test_reopening_the_journal_keeps_the_count(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal", "e1"))
    send(bot, text_event("One line.", "e2"))
    send(bot, text_event("/journal", "e3"))

    assert "already open" in client.last_reply
    assert "1 line(s)" in client.last_reply


def test_a_photo_sent_in_journal_mode_still_lands_in_raw(make_config, now):
    client = FakeClient(content=jpeg_with_exif())
    bot, client = make_bot(make_config, now, client=client)

    send(bot, text_event("journal", "e1"))
    send(bot, image_event("img1"))

    assert "`photo`" in bot.vault.read_raw_zone(DAY)
    # And the mode is still open afterwards.
    send(bot, text_event("Back to writing.", "e2"))
    assert "Back to writing." in bot.vault.read_journal(DAY)


def test_today_reads_back_both_zones(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("note picked up the laundry", "e1"))
    send(bot, text_event("journal A slow, good day.", "e2"))
    send(bot, text_event("today", "e3"))

    assert "1 capture(s)" in client.last_reply
    assert "picked up the laundry" in client.last_reply
    assert "Journal — 1 line(s)" in client.last_reply
    assert "A slow, good day." in client.last_reply


def test_today_without_captures_still_shows_the_journal(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("journal Only wrote, captured nothing.", "e1"))
    send(bot, text_event("today", "e2"))

    assert "nothing captured in RAW yet" in client.last_reply
    assert "Only wrote, captured nothing." in client.last_reply


def test_a_broken_note_is_reported_to_the_sender(make_config, now):
    bot, client = make_bot(make_config, now)
    path = bot.vault.daily_note_path(DAY)
    path.write_text("<!-- journal:start -->\nunbalanced\n", encoding="utf-8")

    send(bot, text_event("journal Anything at all.", "e1"))

    assert "unbalanced" in client.last_reply
    assert "JOURNAL" in client.last_reply


# -- config ------------------------------------------------------------


def test_journal_block_is_read_from_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "journal:\n"
        '  heading: "## Diary"\n'
        '  start_marker: "<!-- diary:start -->"\n'
        '  end_marker: "<!-- diary:end -->"\n'
        "  timestamps: false\n",
        encoding="utf-8",
    )

    config = load_config(config_file, env={"VAULT_PATH": str(tmp_path / "vault")})

    assert config.journal.heading == "## Diary"
    assert config.journal.start_marker == "<!-- diary:start -->"
    assert config.journal.timestamps is False


def test_defaults_when_no_journal_block(tmp_path):
    config = load_config(None, env={"VAULT_PATH": str(tmp_path / "vault")})

    assert config.journal == JournalConfig()


def test_a_marker_shared_with_raw_is_refused(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        'journal:\n  start_marker: "<!-- raw:start -->"\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError):
        load_config(config_file, env={"VAULT_PATH": str(tmp_path / "vault")})


def test_an_empty_marker_is_refused(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text('journal:\n  end_marker: "  "\n', encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(config_file, env={"VAULT_PATH": str(tmp_path / "vault")})
