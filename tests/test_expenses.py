from __future__ import annotations

from test_handlers import DAY, make_bot, photo_bot, send, text_event

from conftest import FakeClient, jpeg_with_exif
from satemshi.line_bot.handlers import _parse_amount


def expense_entries(bot):
    return [e for e in bot.vault.entries(DAY) if e.kind == "expense"]


# -- amount parsing -----------------------------------------------------


def test_parse_amount_variants():
    assert _parse_amount("120") == ("120", "")
    assert _parse_amount("1,250.50") == ("1250.50", "")
    assert _parse_amount("฿120") == ("120", "")
    assert _parse_amount("120 baht lunch at the market") == (
        "120",
        "lunch at the market",
    )
    assert _parse_amount("350 groceries") == ("350", "groceries")


def test_parse_amount_leaves_non_amounts_alone():
    assert _parse_amount("7-eleven run") == (None, "7-eleven run")
    assert _parse_amount("lunch 120") == (None, "lunch 120")
    assert _parse_amount("120lunch") == (None, "120lunch")


# -- the spent flow -----------------------------------------------------


def test_spent_with_amount_and_what_asks_only_category(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("spent 120 lunch at the market", "e1"))
    assert "Category?" in client.last_reply
    assert "1 food" in client.last_reply

    send(bot, text_event("1", "e2"))

    (entry,) = expense_entries(bot)
    assert entry.title == "lunch at the market"
    assert entry.fields["amount"] == "120"
    assert entry.fields["category"] == "food"
    assert "Saved to 2026-08-04.md" in client.last_reply


def test_spent_bare_asks_everything(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("spent", "e1"))
    assert "How much was it?" in client.last_reply

    send(bot, text_event("1,250.50", "e2"))
    assert "Category?" in client.last_reply

    send(bot, text_event("transport", "e3"))
    assert "What was it for?" in client.last_reply

    send(bot, text_event("taxi to the airport", "e4"))

    (entry,) = expense_entries(bot)
    assert entry.fields["amount"] == "1250.50"
    assert entry.fields["category"] == "transport"
    assert entry.title == "taxi to the airport"


def test_amount_answer_with_trailing_text_fills_what(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("spent", "e1"))
    send(bot, text_event("120 for lunch", "e2"))
    assert "Category?" in client.last_reply

    send(bot, text_event("food", "e3"))  # "what" was answered inline

    (entry,) = expense_entries(bot)
    assert entry.fields["amount"] == "120"
    assert entry.title == "for lunch"


def test_thai_spent_alias(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("จ่าย 40 ค่ากาแฟ", "e1"))
    send(bot, text_event("food", "e2"))

    (entry,) = expense_entries(bot)
    assert entry.fields["amount"] == "40"
    assert entry.title == "ค่ากาแฟ"


def test_unknown_category_is_kept_verbatim(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("spent 90 boat fare", "e1"))
    send(bot, text_event("island trips", "e2"))

    (entry,) = expense_entries(bot)
    assert entry.fields["category"] == "island trips"


def test_done_finishes_an_expense_early(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("spent 60 motorbike wash", "e1"))
    send(bot, text_event("done", "e2"))

    (entry,) = expense_entries(bot)
    assert entry.fields["amount"] == "60"
    assert "category" not in entry.fields


def test_cancel_drops_an_expense(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("spent 500 something", "e1"))
    send(bot, text_event("cancel", "e2"))

    assert expense_entries(bot) == []


def test_skipped_category_becomes_uncategorized_in_summary(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("spent 75 snacks", "e1"))
    send(bot, text_event("-", "e2"))
    send(bot, text_event("/expenses", "e3"))

    assert "uncategorized: 75 (1)" in client.last_reply


# -- slips in the photo queue ------------------------------------------


def test_slip_answer_turns_a_photo_into_an_expense(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=2)

    send(bot, text_event("photos", "e1"))
    assert "“slip” records it as an expense" in client.last_reply

    send(bot, text_event("slip", "e2"))
    assert "How much was it?" in client.last_reply

    send(bot, text_event("350", "e3"))
    assert "Category?" in client.last_reply

    send(bot, text_event("3", "e4"))  # groceries
    assert "What was it for?" in client.last_reply

    send(bot, text_event("weekly market run", "e5"))
    assert "IMG_1.jpg" in client.last_reply  # moved on to the next photo

    entries = {e.title: e for e in bot.vault.entries(DAY)}
    slip = entries["IMG_0.jpg"]
    assert slip.kind == "photo"  # still linked to the image file
    assert slip.fields["amount"] == "350"
    assert slip.fields["category"] == "groceries"
    assert slip.fields["what"] == "weekly market run"


def test_summary_totals_span_spent_and_slips(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, text_event("food", "e2"))
    send(bot, text_event("photos", "e3"))
    send(bot, text_event("slip", "e4"))
    send(bot, text_event("380 electricity bill", "e5"))
    send(bot, text_event("bills", "e6"))
    send(bot, text_event("/expenses", "e7"))

    reply = client.last_reply
    assert "2 expense(s), total 500" in reply
    assert "bills: 380 (1)" in reply
    assert "food: 120 (1)" in reply
    assert "electricity bill" in reply


def test_expenses_with_nothing_recorded(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("expenses", "e1"))

    assert "No expenses recorded for 2026-08-04" in client.last_reply


def test_spent_redelivery_writes_once(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, text_event("food", "e2"))
    # LINE redelivers the answer event; the expense entry id comes from
    # the start event, so the second write is a dedupe no-op.
    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, text_event("food", "e2"))

    assert len(expense_entries(bot)) == 1


def test_photo_sent_mid_expense_is_not_an_answer(make_config, now):
    from test_handlers import image_event

    client = FakeClient(content=jpeg_with_exif("2026:08:04 09:15:00"))
    bot, _ = make_bot(make_config, now, client=client)

    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, image_event("img1"))

    photos = [e for e in bot.vault.entries(DAY) if e.kind == "photo"]
    assert len(photos) == 1
    assert "event" not in photos[0].fields  # not tagged with the expense


# -- fixes from the adversarial review ---------------------------------


def test_comma_typo_amounts_are_not_parsed_as_thousands():
    assert _parse_amount("120,50 taxi") == (None, "120,50 taxi")
    assert _parse_amount("1,2,3 stuff") == (None, "1,2,3 stuff")
    assert _parse_amount("1,250.50") == ("1250.50", "")


def test_double_slip_does_not_shift_the_answers(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("slip", "e2"))
    send(bot, text_event("slip", "e3"))  # user double-sends
    assert "How much was it?" in client.last_reply  # re-asked, not consumed

    send(bot, text_event("350", "e4"))
    send(bot, text_event("3", "e5"))
    send(bot, text_event("weekly market run", "e6"))

    (entry,) = bot.vault.entries(DAY)
    assert entry.fields["amount"] == "350"
    assert entry.fields["category"] == "groceries"


def test_redelivered_answer_event_is_consumed_once(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("slip", "e2"))
    send(bot, text_event("slip", "e2"))  # LINE redelivery: same event id
    assert "How much was it?" in client.last_reply

    send(bot, text_event("350", "e3"))
    send(bot, text_event("350", "e3"))  # redelivered mid-flow
    assert "Category?" in client.last_reply

    send(bot, text_event("3", "e4"))
    send(bot, text_event("weekly market run", "e5"))

    (entry,) = bot.vault.entries(DAY)
    assert entry.fields["amount"] == "350"
    assert entry.fields["what"] == "weekly market run"


def test_redelivered_spent_start_is_not_a_category_answer(make_config, now):
    bot, client = make_bot(make_config, now)

    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, text_event("spent 120 lunch", "e1"))  # redelivered start
    assert "Category?" in client.last_reply  # still waiting for it

    send(bot, text_event("1", "e2"))

    (entry,) = expense_entries(bot)
    assert entry.fields["category"] == "food"


def test_multiline_what_stays_one_entry_line(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, text_event("1", "e2"))

    bot2, _ = make_bot(make_config, now)
    send(bot2, text_event("spent", "e3"))
    send(bot2, text_event("60", "e4"))
    send(bot2, text_event("2", "e5"))
    send(bot2, text_event("market\n- **08:00** `event` FORGED ^raw-evil", "e6"))

    entries = bot2.vault.entries(DAY)
    assert len(entries) == 2  # no forged third entry
    assert all("FORGED" not in e.anchor for e in entries)
    forged = [e for e in entries if "FORGED" in e.title]
    assert len(forged) == 1  # kept, but flattened into the title
    assert "\n" not in forged[0].title


def test_multiline_slip_answer_does_not_corrupt_the_zone(
    make_config, now, tmp_path
):
    bot, _ = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("slip", "e2"))
    send(bot, text_event("350", "e3"))
    send(bot, text_event("3", "e4"))
    send(bot, text_event("groceries:\n- eggs\n- milk", "e5"))

    (entry,) = bot.vault.entries(DAY)
    assert entry.fields["what"] == "groceries: / - eggs / - milk"
    # And the zone parses back to exactly one entry.
    zone = bot.vault.read_raw_zone(DAY)
    assert zone.count("^raw-") == 1


def test_superscript_digit_category_does_not_crash(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, text_event("²", "e2"))  # isdigit() but not int()-able

    (entry,) = expense_entries(bot)
    assert entry.fields["category"] == "²"  # kept verbatim, no crash


def test_thai_numeral_picks_a_category(make_config, now):
    bot, _ = make_bot(make_config, now)

    send(bot, text_event("spent 120 lunch", "e1"))
    send(bot, text_event("๓", "e2"))  # Thai 3 -> groceries

    (entry,) = expense_entries(bot)
    assert entry.fields["category"] == "groceries"


def test_location_mid_slip_is_not_the_amount(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("slip", "e2"))
    send(
        bot,
        {
            "type": "message",
            "webhookEventId": "e3",
            "replyToken": "r3",
            "source": {"userId": "U1234"},
            "message": {"type": "location", "id": "loc", "title": "Railay Beach"},
        },
    )

    entries = bot.vault.entries(DAY)
    photo = next(e for e in entries if e.kind == "photo")
    assert "amount" not in photo.fields  # the location was not the amount
    location = next(e for e in entries if e.kind == "location")
    assert location.title == "Railay Beach"  # recorded on its own

    send(bot, text_event("350", "e4"))  # the flow is still on amount
    assert "Category?" in client.last_reply


def test_skipped_amount_slip_still_shows_in_expenses(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("slip", "e2"))
    send(bot, text_event("-", "e3"))  # skip the amount
    send(bot, text_event("3", "e4"))
    send(bot, text_event("weekly market run", "e5"))
    send(bot, text_event("expenses", "e6"))

    reply = client.last_reply
    assert "1 expense(s)" in reply
    assert "groceries: ? (1)" in reply  # no false zero
    assert "1 without a readable amount" in reply


def test_completed_slip_is_not_resweep_requeued(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("slip", "e2"))
    send(bot, text_event("350", "e3"))
    send(bot, text_event("3", "e4"))
    send(bot, text_event("-", "e5"))  # skip "what": no photo-slot key lands
    send(bot, text_event("photos", "e6"))

    assert "already have answers" in client.last_reply


def test_next_mid_slip_discards_the_partial_amount(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=2)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("slip", "e2"))
    send(bot, text_event("350", "e3"))
    send(bot, text_event("next", "e4"))  # skip the photo after all

    photo = next(e for e in bot.vault.entries(DAY) if e.title == "IMG_0.jpg")
    assert "amount" not in photo.fields  # no phantom half-expense
    send(bot, text_event("/expenses", "e5"))
    assert "No expenses recorded" in client.last_reply


def test_spent_mid_queue_is_refused_not_clobbering(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("photos", "e1"))
    send(bot, text_event("/spent 120 lunch", "e2"))
    assert "mid-capture" in client.last_reply
    assert expense_entries(bot) == []

    # The photo queue is still live and on the same question.
    send(bot, text_event("Ao Nang", "e3"))
    assert "What is it?" in client.last_reply


def test_photos_mid_event_capture_does_not_clobber(make_config, now, tmp_path):
    bot, client = photo_bot(make_config, now, tmp_path, count=1)

    send(bot, text_event("Team lunch", "e1"))  # event capture opens
    send(bot, text_event("/photos", "e2"))
    assert "finish it" in client.last_reply

    send(bot, text_event("12:30", "e3"))  # still answering the event
    assert "Who was there?" in client.last_reply
