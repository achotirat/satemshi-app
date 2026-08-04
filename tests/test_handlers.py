from __future__ import annotations

from datetime import date

from conftest import FakeClient, jpeg_with_exif, run
from satemshi.config import LineBotConfig, PhotosConfig
from satemshi.line_bot.handlers import CaptureBot

DAY = date(2026, 8, 4)
USER = "U1234"


def text_event(text: str, event_id: str = "e1") -> dict:
    return {
        "type": "message",
        "webhookEventId": event_id,
        "replyToken": f"r-{event_id}",
        "source": {"type": "user", "userId": USER},
        "message": {"type": "text", "id": f"m-{event_id}", "text": text},
    }


def image_event(message_id: str = "img1") -> dict:
    return {
        "type": "message",
        "webhookEventId": f"e-{message_id}",
        "replyToken": f"r-{message_id}",
        "source": {"type": "user", "userId": USER},
        "message": {"type": "image", "id": message_id},
    }


def make_bot(
    make_config, now, client=None, **overrides
) -> tuple[CaptureBot, FakeClient]:
    client = client or FakeClient()
    bot = CaptureBot(make_config(**overrides), client, now=now)
    return bot, client


def send(bot: CaptureBot, event: dict) -> None:
    run(bot.handle_event(event))


# -- the event capture conversation ------------------------------------


def test_slot_filling_writes_one_entry_at_the_end(make_config, now, vault):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("Lunch with the Krabi team", "e1"))
    assert "When did it happen?" in client.last_reply
    assert bot.vault.read_raw_zone(DAY) == ""  # nothing written mid-flow

    send(bot, text_event("12:30", "e2"))
    assert "Who was there?" in client.last_reply

    send(bot, text_event("Nok, Bank", "e3"))

    zone = bot.vault.read_raw_zone(DAY)
    assert "`event` Lunch with the Krabi team" in zone
    assert "- when:: 12:30" in zone
    assert "- who:: Nok, Bank" in zone
    assert "- source:: line/U1234" in zone
    assert "Saved to 2026-08-04.md" in client.last_reply


def test_dash_skips_a_slot(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("Beach walk", "e1"))
    send(bot, text_event("-", "e2"))
    send(bot, text_event("alone", "e3"))

    zone = bot.vault.read_raw_zone(DAY)
    assert "when::" not in zone
    assert "- who:: alone" in zone


def test_done_finishes_early(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("Ran into a supplier", "e1"))
    send(bot, text_event("done", "e2"))

    zone = bot.vault.read_raw_zone(DAY)
    assert "Ran into a supplier" in zone
    assert "when::" not in zone


def test_cancel_writes_nothing(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("Something forgettable", "e1"))
    send(bot, text_event("cancel", "e2"))

    assert bot.vault.read_raw_zone(DAY) == ""
    assert "Dropped" in client.last_reply


def test_a_command_word_mid_capture_is_an_answer(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("Morning swim", "e1"))
    send(bot, text_event("today", "e2"))  # answering "when", not the command

    assert "Who was there?" in client.last_reply

    send(bot, text_event("/today", "e3"))  # explicit command still works
    assert "Who was there?" not in client.last_reply


def test_note_captures_verbatim_without_questions(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("note bought 2kg of mangoes", "e1"))

    zone = bot.vault.read_raw_zone(DAY)
    assert "`note` bought 2kg of mangoes" in zone
    assert "When did it happen?" not in client.last_reply


def test_redelivered_webhook_event_writes_once(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("note same thing", "e1"))
    send(bot, text_event("note same thing", "e1"))

    assert bot.vault.read_raw_zone(DAY).count("same thing") == 1


def test_disallowed_sender_is_refused(make_config, now, session_dir):
    bot, client = make_bot(
        make_config,
        now,
        line_bot=LineBotConfig(
            session_dir=session_dir, allowed_user_ids=("Usomeone-else",)
        ),
    )

    send(bot, text_event("note secret", "e1"))

    assert bot.vault.read_raw_zone(DAY) == ""
    assert "Not an allowed sender" in client.last_reply


# -- photos -------------------------------------------------------------


def test_image_is_stored_and_recorded(make_config, now, vault):
    client = FakeClient(content=jpeg_with_exif("2026:08:04 09:15:00"))
    bot, _ = make_bot(make_config, now, client=client)

    send(bot, image_event("img1"))

    stored = vault / "Attachments" / "2026" / "08" / "20260804-091500-line-img1.jpg"
    assert stored.is_file()
    zone = bot.vault.read_raw_zone(DAY)
    assert "`photo`" in zone
    assert "file:: ![[Attachments/2026/08/20260804-091500-line-img1.jpg]]" in zone
    assert "taken:: 2026-08-04 09:15" in zone


def test_image_sent_mid_capture_is_tagged_with_the_event(make_config, now):
    client = FakeClient(content=jpeg_with_exif("2026:08:04 09:15:00"))
    bot, _ = make_bot(make_config, now, client=client)

    send(bot, text_event("Site visit", "e1"))
    send(bot, image_event("img1"))

    assert "- event:: Site visit" in bot.vault.read_raw_zone(DAY)


def test_a_failed_download_does_not_write_a_broken_entry(make_config, now):
    client = FakeClient()
    client.fail_content = True
    bot, _ = make_bot(make_config, now, client=client)

    send(bot, image_event("img1"))

    assert bot.vault.read_raw_zone(DAY) == ""
    assert "couldn't download" in client.last_reply


def test_photos_command_sweeps_source_dirs_into_raw(make_config, now, tmp_path):
    phone = tmp_path / "phone"
    phone.mkdir()
    (phone / "IMG_9.jpg").write_bytes(jpeg_with_exif("2026:08:04 07:00:00"))
    (phone / "IMG_8.jpg").write_bytes(jpeg_with_exif("2019:05:05 07:00:00"))

    bot, client = make_bot(
        make_config, now, photos=PhotosConfig(source_dirs=(str(phone),))
    )

    send(bot, text_event("photos", "e1"))

    assert "1 photo(s) for 2026-08-04, 1 newly recorded" in client.last_reply
    zone = bot.vault.read_raw_zone(DAY)
    assert "IMG_9.jpg" in zone
    assert "IMG_8.jpg" not in zone

    # Sweeping again finds the same photo but records nothing new.
    send(bot, text_event("photos", "e2"))
    assert "0 newly recorded" in client.last_reply
    assert bot.vault.read_raw_zone(DAY).count("^raw-photo-") == 1


def test_photos_command_with_nothing_to_find(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("photos", "e1"))

    assert "No photos found for 2026-08-04" in client.last_reply


# -- other event types --------------------------------------------------


def test_location_answers_the_where_slot(make_config, now, session_dir):
    from satemshi.config import EventSlot

    bot, _ = make_bot(
        make_config,
        now,
        line_bot=LineBotConfig(
            session_dir=session_dir,
            event_slots=(EventSlot("where", "Where was it?"),),
        ),
    )
    send(bot, text_event("Dinner out", "e1"))
    send(
        bot,
        {
            "type": "message",
            "webhookEventId": "e2",
            "replyToken": "r2",
            "source": {"type": "user", "userId": USER},
            "message": {
                "type": "location",
                "id": "loc1",
                "title": "Ao Nang",
                "address": "Krabi",
                "latitude": 8.03,
                "longitude": 98.82,
            },
        },
    )

    zone = bot.vault.read_raw_zone(DAY)
    assert "`event` Dinner out" in zone
    assert "- where:: Ao Nang" in zone


def test_standalone_location_is_its_own_entry(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(
        bot,
        {
            "type": "message",
            "webhookEventId": "e9",
            "replyToken": "r9",
            "source": {"type": "user", "userId": USER},
            "message": {
                "type": "location",
                "id": "loc9",
                "title": "Casa de Yim",
                "address": "Ao Nang, Krabi",
                "latitude": 8.03,
                "longitude": 98.82,
            },
        },
    )

    zone = bot.vault.read_raw_zone(DAY)
    assert "`location` Casa de Yim" in zone
    assert "- address:: Ao Nang, Krabi" in zone
    assert "- latitude:: 8.03" in zone


def test_follow_event_reports_the_user_id(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, {"type": "follow", "replyToken": "r", "source": {"userId": USER}})

    assert USER in client.last_reply


def test_today_reads_back_the_zone(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("note picked up the laundry", "e1"))
    send(bot, text_event("today", "e2"))

    assert "2026-08-04 — 1 capture(s)" in client.last_reply
    assert "picked up the laundry" in client.last_reply


def test_unsupported_message_type_is_declined(make_config, now):
    bot, client = make_bot(make_config, now)

    send(
        bot,
        {
            "type": "message",
            "webhookEventId": "e1",
            "replyToken": "r1",
            "source": {"userId": USER},
            "message": {"type": "sticker", "id": "s1"},
        },
    )

    assert "can't capture a sticker" in client.last_reply
    assert bot.vault.read_raw_zone(DAY) == ""
