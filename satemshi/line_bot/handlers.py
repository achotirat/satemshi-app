"""Turning LINE webhook events into RAW captures.

The flow the bot runs, in one place:

- plain text starts an event capture and the bot asks the configured
  follow-up questions one at a time (``-`` skips one, ``done`` finishes
  early, ``cancel`` throws the draft away);
- an image is downloaded, stored in the vault and recorded in RAW;
- ``photos`` sweeps today's photos — the ones sent to the bot and the
  ones sitting in the configured source directories — and records any
  that are not in RAW yet;
- ``today`` reads back what has been captured so far.

Every branch writes to the vault *before* it replies: a capture that
made it to disk is not lost just because the reply token expired.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from ..config import Config
from ..models import RawEntry
from ..photos import Photo, PhotoStore, exif_datetime, suffix_for_content_type
from ..vault import VaultWriter
from .client import MessagingClient
from .sessions import Session, SessionStore

logger = logging.getLogger(__name__)

SKIP_TOKENS = {"-", "skip", "ข้าม"}
NEXT_TOKENS = {"next", "ถัดไป"}
SPENT_COMMANDS = {"spent", "spend", "จ่าย"}
SLIP_TOKENS = {"slip", "สลิป"}

# The three things an expense needs, asked in this order (minus whatever
# was already said inline or is disabled in config).
EXPENSE_ORDER = ("amount", "category", "what")

# "120", "1,250.50", "฿120", "120 baht lunch" — an amount, an optional
# currency mark, and whatever text follows. "7-eleven run" must NOT
# match (the digits have to stand alone), and neither must "120,50" —
# commas are only thousands separators, so a misplaced one must not
# silently become a 100x amount.
_AMOUNT_RE = re.compile(
    r"^฿?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"(?:\s*(?:฿|บาท|baht|thb))?(?:\s+(.+))?$",
    re.IGNORECASE,
)

HELP_TEXT = (
    "Send me anything about today and it lands in your vault's RAW zone.\n\n"
    "• any text — starts an event capture, then I ask a few follow-ups\n"
    "• a photo — saved to the vault and recorded in RAW\n"
    "• a location — recorded, or used as the answer to “where”\n\n"
    "Commands:\n"
    "• today — read back today's RAW zone\n"
    "• photos — find today's photos, then ask about each one\n"
    "• spent <amount> <what> — record an expense, then pick a category\n"
    "• expenses — today's spending by category\n"
    "• note <text> — capture verbatim, no questions\n"
    "• next — skip to the next photo\n"
    "• done — finish the current capture now\n"
    "• cancel — throw the current capture away\n"
    "• whoami — show my LINE user id\n"
    "• help — this message"
)


class CaptureBot:
    def __init__(
        self,
        config: Config,
        client: MessagingClient,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self._now = now or (lambda: datetime.now(config.tzinfo))
        self.vault = VaultWriter(config.vault_path, config.raw)
        self.photos = PhotoStore(config.vault_path, config.photos, config.tzinfo)
        self.sessions = SessionStore(
            _session_dir(config.line_bot.session_dir),
            config.line_bot.session_ttl_seconds,
        )

    # -- entry point ---------------------------------------------------

    async def handle_event(self, event: dict) -> None:
        source = event.get("source") or {}
        user_id = source.get("userId")
        event_type = event.get("type")

        if event_type == "follow":
            await self._reply(
                event,
                "Connected. I capture into your vault's RAW zone.\n"
                f"Your LINE user id is {user_id or 'unknown'} — add it to "
                "line_bot.allowed_user_ids in config.yaml.\n\n" + HELP_TEXT,
            )
            return

        if event_type != "message":
            return

        if not self.config.line_bot.is_allowed(user_id):
            logger.warning("Ignoring message from disallowed user %s", user_id)
            await self._reply(event, "Not an allowed sender for this vault.")
            return

        message = event.get("message") or {}
        kind = message.get("type")
        if kind == "text":
            await self._on_text(event, user_id, message.get("text", ""))
        elif kind == "image":
            await self._on_image(event, user_id, message)
        elif kind == "location":
            await self._on_location(event, user_id, message)
        else:
            await self._reply(
                event, f"I can't capture a {kind} message yet. Send text or a photo."
            )

    # -- text ----------------------------------------------------------

    async def _on_text(self, event: dict, user_id: str | None, text: str) -> None:
        body = text.strip()
        if not body:
            return

        session = self.sessions.get(user_id) if user_id else None
        head, _, argument = body.partition(" ")
        explicit = head.startswith("/")
        command = head.lstrip("/").lower()
        argument = argument.strip()

        # Mid-capture, a bare word is an answer, not a command — "today"
        # is a perfectly good answer to "when did it happen?". Only
        # "done"/"cancel" and slash-prefixed commands still act as
        # commands while a capture is in progress.
        as_command = session is None or explicit or command in ("done", "cancel")

        if as_command:
            if command in ("help", "?"):
                await self._reply(event, HELP_TEXT)
                return
            if command == "whoami":
                await self._reply(
                    event, f"Your LINE user id is {user_id or 'unknown'}."
                )
                return
            if command == "today":
                await self._reply(event, self._today_summary())
                return
            if command == "photos":
                await self._reply(event, self._sweep_photos(user_id))
                return
            if command in SPENT_COMMANDS:
                await self._start_expense(event, user_id, argument)
                return
            if command == "expenses":
                await self._reply(event, self._expenses_summary())
                return
            if command == "cancel":
                if user_id:
                    self.sessions.clear(user_id)
                await self._reply(
                    event,
                    "Dropped. Nothing was written."
                    if session is not None
                    else "Nothing in progress.",
                )
                return
            if command == "note":
                await self._capture_note(event, user_id, argument or "(empty note)")
                return
            if command == "done":
                if session is None:
                    await self._reply(
                        event, "Nothing in progress. Send some text to start."
                    )
                elif session.kind == "photo":
                    await self._finish_photo_queue(event, session)
                elif session.kind == "expense":
                    await self._finalise_expense(event, session)
                else:
                    await self._finalise(event, session)
                return

        if session is not None:
            if self._is_redelivery(event, session):
                return
            if session.kind == "photo":
                await self._answer_photo_slot(event, session, body)
            elif session.kind == "expense":
                await self._answer_expense_slot(event, session, body)
            else:
                await self._answer_slot(event, session, body)
            return

        await self._start_capture(
            event, user_id, argument if command == "event" else body
        )

    def _is_redelivery(self, event: dict, session: Session) -> bool:
        """LINE redelivers webhook events. Mid-conversation, consuming
        the same event twice would shift every later answer by one slot,
        so an id we have already consumed — or the event that started
        the session — is dropped here. The id is stamped on the session
        now and persisted by whichever save the dispatch path performs.
        """
        event_id = _entry_id(event)
        if not event_id:
            return False
        if event_id in (session.entry_id, session.last_event_id):
            return True
        session.last_event_id = event_id
        return False

    async def _start_capture(
        self, event: dict, user_id: str | None, title: str
    ) -> None:
        title = title.strip() or "(untitled)"
        entry_id = _entry_id(event)
        slots = self.config.line_bot.event_slots

        if user_id is None or not slots:
            # No user to hold a conversation with (or nothing to ask):
            # write it straight through rather than dropping it.
            await self._write_and_confirm(
                event,
                RawEntry(
                    at=self._now(),
                    kind="event",
                    title=title,
                    entry_id=entry_id,
                    fields=self._with_source({}, user_id),
                ),
            )
            return

        session = Session(user_id=user_id, entry_id=entry_id, title=title)
        self.sessions.save(session)
        await self._reply(
            event,
            f"Got it: “{title}”.\n{slots[0].question}\n"
            "(“-” to skip, “done” to finish, “cancel” to drop)",
        )

    async def _answer_slot(self, event: dict, session: Session, answer: str) -> None:
        slots = self.config.line_bot.event_slots
        slot = slots[session.slot_index]
        if answer.strip().lower() not in SKIP_TOKENS:
            session.answers[slot.key] = answer.strip()
        session.slot_index += 1

        if session.slot_index >= len(slots):
            await self._finalise(event, session)
            return

        self.sessions.save(session)
        await self._reply(event, slots[session.slot_index].question)

    async def _finalise(self, event: dict, session: Session) -> None:
        entry = RawEntry(
            at=self._now(),
            kind="event",
            title=session.title,
            entry_id=session.entry_id,
            fields=self._with_source(dict(session.answers), session.user_id),
        )
        self.sessions.clear(session.user_id)
        await self._write_and_confirm(event, entry)

    async def _capture_note(
        self, event: dict, user_id: str | None, text: str
    ) -> None:
        await self._write_and_confirm(
            event,
            RawEntry(
                at=self._now(),
                kind="note",
                title=text,
                entry_id=_entry_id(event),
                fields=self._with_source({}, user_id),
            ),
        )

    # -- image ---------------------------------------------------------

    async def _on_image(self, event: dict, user_id: str | None, message: dict) -> None:
        message_id = message.get("id") or _entry_id(event)
        try:
            data, content_type = await self.client.get_content(message_id)
        except Exception:  # network/API failure — keep serving
            logger.exception("Could not download image %s", message_id)
            await self._reply(event, "I couldn't download that photo — try again?")
            return

        naive = exif_datetime(data)
        # EXIF carries no timezone, so read it as a local wall clock.
        when = (
            naive.replace(tzinfo=self.config.tzinfo)
            if naive is not None
            else self._now()
        )
        photo = self.photos.store(
            data, when, f"line-{message_id}", suffix_for_content_type(content_type)
        )

        fields = {"file": f"![[{photo.display}]]", "taken": f"{when:%Y-%m-%d %H:%M}"}
        session = self.sessions.get(user_id) if user_id else None
        if session is not None and session.kind != "event":
            session = None  # mid photo-queue: this is a new capture, not an answer
        if session is not None:
            fields["event"] = session.title
        await self._write_and_confirm(
            event,
            RawEntry(
                at=when,
                kind="photo",
                title=session.title if session else "photo",
                entry_id=f"line-{message_id}",
                fields=self._with_source(fields, user_id),
            ),
            day_hint=when,
        )

    # -- location ------------------------------------------------------

    async def _on_location(
        self, event: dict, user_id: str | None, message: dict
    ) -> None:
        place = (message.get("title") or message.get("address") or "location").strip()
        session = self.sessions.get(user_id) if user_id else None
        slots = self.config.line_bot.event_slots

        if session is not None and self._is_redelivery(event, session):
            return

        if (
            session is not None
            and session.kind == "photo"
            and session.queue
            and session.queue[0].get("mode") != "expense"
        ):
            # A location dropped while going through photos answers the
            # question on the table — usually "where was this taken?".
            # Mid-slip it is NOT the amount, so it falls through to a
            # plain location entry instead.
            await self._answer_photo_slot(event, session, place)
            return

        if (
            session is not None
            and session.kind == "event"
            and session.slot_index < len(slots)
        ):
            # A location sent mid-capture is the answer to "where".
            session.answers["where"] = place
            if message.get("address"):
                session.answers["address"] = str(message["address"])
            if slots[session.slot_index].key == "where":
                await self._answer_slot(event, session, place)
                return
            self.sessions.save(session)
            await self._reply(event, f"Noted “where” as {place}.")
            return

        fields = {
            "address": str(message.get("address") or ""),
            "latitude": str(message.get("latitude") or ""),
            "longitude": str(message.get("longitude") or ""),
        }
        await self._write_and_confirm(
            event,
            RawEntry(
                at=self._now(),
                kind="location",
                title=place,
                entry_id=_entry_id(event),
                fields=self._with_source(fields, user_id),
            ),
        )

    # -- commands ------------------------------------------------------

    def _today_summary(self) -> str:
        today = self._now().date()
        zone = self.vault.read_raw_zone(today)
        if not zone:
            return f"Nothing captured for {today.isoformat()} yet."
        count = sum(1 for line in zone.splitlines() if line.startswith("- "))
        return f"{today.isoformat()} — {count} capture(s):\n\n{zone}"

    def _sweep_photos(self, user_id: str | None) -> str:
        today = self._now().date()
        found = self.photos.find_for_day(today)
        if not found:
            roots = len(self.config.photos.source_dirs) + 1
            return (
                f"No photos found for {today.isoformat()} across {roots} "
                "location(s). Send one here and I'll file it."
            )

        # A photo sent to the bot is already in RAW under its own anchor,
        # and the sweep sees the same file again in the attachments
        # directory — so match on the link, not just the anchor.
        zone = self.vault.read_raw_zone(today)
        added = 0
        for photo in found:
            if _photo_link(photo) in zone:
                continue
            if self.vault.append(_photo_entry(photo), day=today):
                added += 1

        listing = "\n".join(
            f"• {photo.taken_at:%H:%M} — {photo.display} ({photo.source})"
            for photo in found
        )
        header = (
            f"{len(found)} photo(s) for {today.isoformat()}, "
            f"{added} newly recorded in RAW:\n\n{listing}"
        )
        return header + self._start_photo_questions(user_id, today)

    # -- asking about the photos ---------------------------------------

    def _start_photo_questions(self, user_id: str | None, day: date) -> str:
        """Queue up the photos that have no answers on them yet."""
        slots = self.config.line_bot.photo_slots
        if user_id is None or not slots:
            return ""

        existing = self.sessions.get(user_id)
        if existing is not None and existing.kind != "photo":
            return (
                "\n\nYou're mid-capture — finish it (“done”) or drop it "
                "(“cancel”), then run “photos” again to go through these."
            )
        if existing is not None:
            # Re-running "photos" mid-queue: keep what has been answered
            # for the current photo before the queue is rebuilt.
            self._store_photo_answers(existing)

        # Answered = carrying any photo-slot key or any expense field
        # (a slip keeps its photo kind but is answered all the same).
        keys = {slot.key for slot in slots} | set(EXPENSE_ORDER)
        queue = [
            {
                "anchor": entry.anchor,
                "name": entry.title,
                "at": entry.time,
                "day": day.isoformat(),
            }
            for entry in self.vault.entries(day)
            if entry.kind == "photo" and not keys & set(entry.fields)
        ]
        if not queue:
            return "\n\nAll of them already have answers on them."

        session = Session(
            user_id=user_id,
            entry_id=f"photos-{day.isoformat()}",
            title="photos",
            kind="photo",
            queue=queue,
        )
        self.sessions.save(session)
        return (
            f"\n\n{len(queue)} still unanswered — let's go through them.\n"
            f"(“-” skips a question, “next” skips the photo, “slip” records "
            f"it as an expense, “done” stops)\n\n"
            f"{self._photo_prompt(session)}"
        )

    def _photo_prompt(self, session: Session) -> str:
        photo = session.queue[0]
        slot = self.config.line_bot.photo_slots[session.slot_index]
        remaining = f" [{len(session.queue)} left]" if len(session.queue) > 1 else ""
        return f"{photo['name']} ({photo['at']}){remaining}\n{slot.question}"

    async def _answer_photo_slot(
        self, event: dict, session: Session, answer: str
    ) -> None:
        slots = self.config.line_bot.photo_slots
        cleaned = answer.strip()

        if cleaned.lower() in NEXT_TOKENS:
            await self._advance_photo(event, session, skipped=True)
            return

        photo = session.queue[0]
        if cleaned.lower() in SLIP_TOKENS:
            if photo.get("mode") == "expense":
                # A double-sent "slip" is not the amount — re-ask.
                pending = _pending_of(photo)
                if pending:
                    await self._reply(
                        event,
                        "Already on it.\n" + self._expense_question(pending[0]),
                    )
                    return
                await self._advance_photo(event, session)
                return
            await self._switch_photo_to_expense(event, session, photo)
            return
        if photo.get("mode") == "expense":
            await self._answer_photo_expense(event, session, cleaned)
            return

        if cleaned.lower() not in SKIP_TOKENS:
            session.answers[slots[session.slot_index].key] = cleaned
        session.slot_index += 1

        if session.slot_index >= len(slots):
            await self._advance_photo(event, session)
            return

        self.sessions.save(session)
        await self._reply(event, slots[session.slot_index].question)

    async def _switch_photo_to_expense(
        self, event: dict, session: Session, photo: dict
    ) -> None:
        """This photo is a payment slip: switch its remaining questions
        to the expense set. The answers still land on the photo's own
        RAW entry, next to the file link — and anything already on that
        entry (a slip answered halfway on an earlier run) is not asked
        again.
        """
        day = date.fromisoformat(photo["day"])
        on_entry = next(
            (
                entry.fields
                for entry in self.vault.entries(day)
                if entry.anchor == photo["anchor"]
            ),
            {},
        )
        pending = [key for key in self._expense_pending({}) if key not in on_entry]
        if not pending:
            await self._reply(
                event, "That one already has its expense answers — moving on."
            )
            await self._advance_photo(event, session)
            return

        photo["mode"] = "expense"
        photo["pending"] = ",".join(pending)
        self.sessions.save(session)
        await self._reply(
            event,
            "Recording it as an expense.\n" + self._expense_question(pending[0]),
        )

    async def _answer_photo_expense(
        self, event: dict, session: Session, answer: str
    ) -> None:
        photo = session.queue[0]
        pending = _pending_of(photo)
        if not pending:
            await self._advance_photo(event, session)
            return
        self._apply_expense_answer(session.answers, pending, answer)
        photo["pending"] = ",".join(pending)

        if not pending:
            await self._advance_photo(event, session)
            return
        self.sessions.save(session)
        await self._reply(event, self._expense_question(pending[0]))

    async def _advance_photo(
        self, event: dict, session: Session, skipped: bool = False
    ) -> None:
        """Write the answers onto the current photo, then move to the next.

        "next" means *skip this photo*: whatever was answered for it is
        discarded, not half-recorded.
        """
        written = False if skipped else self._store_photo_answers(session)
        session.queue.pop(0)
        session.slot_index = 0
        session.answers = {}

        if not session.queue:
            self.sessions.clear(session.user_id)
            await self._reply(event, _photo_queue_done(written, skipped))
            return

        self.sessions.save(session)
        if skipped:
            prefix = "Skipped."
        elif written:
            prefix = "Saved."
        else:
            prefix = "Nothing new for that one."
        await self._reply(event, f"{prefix}\n\n{self._photo_prompt(session)}")

    async def _finish_photo_queue(self, event: dict, session: Session) -> None:
        self._store_photo_answers(session)
        remaining = len(session.queue) - 1
        self.sessions.clear(session.user_id)
        await self._reply(
            event,
            f"Stopped. {remaining} photo(s) left unanswered — "
            "run “photos” again to pick up where we left off.",
        )

    def _store_photo_answers(self, session: Session) -> bool:
        if not session.answers or not session.queue:
            return False
        photo = session.queue[0]
        return self.vault.add_fields(
            date.fromisoformat(photo["day"]),
            photo["anchor"],
            self._with_source(dict(session.answers), session.user_id),
        )

    # -- expenses ------------------------------------------------------

    def _expense_pending(self, answers: dict[str, str]) -> list[str]:
        return [
            key
            for key in EXPENSE_ORDER
            if key not in answers
            and not (key == "category" and not self.config.line_bot.expense_categories)
        ]

    def _expense_question(self, key: str) -> str:
        if key == "amount":
            return "How much was it? (just the number, e.g. 120 or 1,250.50)"
        if key == "category":
            categories = self.config.line_bot.expense_categories
            listing = "  ".join(
                f"{index + 1} {name}" for index, name in enumerate(categories)
            )
            return f"Category? Reply a number or a name:\n{listing}"
        return "What was it for?"

    def _resolve_category(self, answer: str) -> str:
        """A number picks from the list, a name matches it, anything
        else is kept as a free-form category."""
        categories = self.config.line_bot.expense_categories
        cleaned = answer.strip()
        # isdecimal, not isdigit: "²" is a digit int() rejects, while
        # Thai numerals ("๓") are decimal and pick from the list fine.
        if cleaned.isdecimal() and 1 <= int(cleaned) <= len(categories):
            return categories[int(cleaned) - 1]
        for name in categories:
            if name.lower() == cleaned.lower():
                return name
        return cleaned

    def _apply_expense_answer(
        self, answers: dict[str, str], pending: list[str], answer: str
    ) -> None:
        key = pending.pop(0)
        cleaned = answer.strip()
        if cleaned.lower() in SKIP_TOKENS:
            return
        if key == "amount":
            amount, rest = _parse_amount(cleaned)
            answers["amount"] = amount or cleaned
            # "120 for lunch" answers two questions at once.
            if amount and rest and "what" in pending:
                answers["what"] = rest
                pending.remove("what")
        elif key == "category":
            answers["category"] = self._resolve_category(cleaned)
        else:
            answers[key] = cleaned

    async def _start_expense(
        self, event: dict, user_id: str | None, text: str
    ) -> None:
        if user_id and self.sessions.get(user_id) is not None:
            # Starting a new flow would silently clobber the open one
            # (one session per user) and lose its answers. Refuse.
            await self._reply(
                event,
                "You're mid-capture — “done” finishes it, “cancel” drops "
                "it. Then record the expense.",
            )
            return

        answers: dict[str, str] = {}
        amount, rest = _parse_amount(text.strip()) if text.strip() else (None, "")
        if amount:
            answers["amount"] = amount
            if rest:
                answers["what"] = rest
        elif text.strip():
            answers["what"] = text.strip()

        pending = self._expense_pending(answers)
        entry_id = _entry_id(event)

        if user_id is None or not pending:
            await self._write_expense(event, entry_id, answers, user_id)
            return

        session = Session(
            user_id=user_id,
            entry_id=entry_id,
            title=answers.get("what", ""),
            kind="expense",
            answers=answers,
            queue=[{"slot": key} for key in pending],
        )
        self.sessions.save(session)
        await self._reply(event, self._expense_question(pending[0]))

    async def _answer_expense_slot(
        self, event: dict, session: Session, answer: str
    ) -> None:
        pending = [item["slot"] for item in session.queue]
        if not pending:
            await self._finalise_expense(event, session)
            return
        self._apply_expense_answer(session.answers, pending, answer)
        session.queue = [{"slot": key} for key in pending]

        if not pending:
            await self._finalise_expense(event, session)
            return
        self.sessions.save(session)
        await self._reply(event, self._expense_question(pending[0]))

    async def _finalise_expense(self, event: dict, session: Session) -> None:
        self.sessions.clear(session.user_id)
        await self._write_expense(
            event, session.entry_id, dict(session.answers), session.user_id
        )

    async def _write_expense(
        self, event: dict, entry_id: str, answers: dict[str, str], user_id: str | None
    ) -> None:
        fields = dict(answers)
        title = fields.pop("what", "") or "(expense)"
        await self._write_and_confirm(
            event,
            RawEntry(
                at=self._now(),
                kind="expense",
                title=title,
                entry_id=entry_id,
                fields=self._with_source(fields, user_id),
            ),
        )

    def _expenses_summary(self) -> str:
        today = self._now().date()
        rows = [
            entry
            for entry in self.vault.entries(today)
            if entry.kind == "expense"
            # A slip keeps its photo kind; count it whichever expense
            # field made it onto the entry (an amount can be skipped).
            or "amount" in entry.fields
            or "category" in entry.fields
        ]
        if not rows:
            return (
                f"No expenses recorded for {today.isoformat()} yet.\n"
                "“spent 120 lunch” records one, and answering “slip” to a "
                "photo records that slip as one."
            )

        total = 0.0
        unparsed = 0
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        detail: list[str] = []
        for entry in rows:
            category = entry.fields.get("category") or "uncategorized"
            raw_amount = entry.fields.get("amount", "")
            value = _to_number(raw_amount)
            counts[category] = counts.get(category, 0) + 1
            if value is None:
                unparsed += 1
            else:
                total += value
                sums[category] = sums.get(category, 0.0) + value
            label = entry.fields.get("what") or entry.title
            detail.append(
                f"• {entry.time} — {raw_amount or '?'} {category} — {label}"
            )

        by_size = sorted(counts, key=lambda name: -sums.get(name, 0.0))
        categories = "\n".join(
            # "?" over a false 0 when no amount in it could be read.
            f"{name}: {_fmt_amount(sums[name]) if name in sums else '?'} "
            f"({counts[name]})"
            for name in by_size
        )
        head = (
            f"{today.isoformat()} — {len(rows)} expense(s), "
            f"total {_fmt_amount(total)}"
        )
        if unparsed:
            head += f" ({unparsed} without a readable amount)"
        return f"{head}\n{categories}\n\n" + "\n".join(detail)

    # -- plumbing ------------------------------------------------------

    def _with_source(
        self, fields: dict[str, str], user_id: str | None
    ) -> dict[str, str]:
        fields["source"] = f"line/{user_id}" if user_id else "line"
        return fields

    async def _write_and_confirm(
        self, event: dict, entry: RawEntry, day_hint: datetime | None = None
    ) -> None:
        day = (day_hint or entry.at).date()
        try:
            written = self.vault.append(entry, day=day)
        except OSError:
            logger.exception("Could not write to the vault")
            await self._reply(
                event, "I couldn't write to the vault — the capture was not saved."
            )
            return

        if not written:
            return  # A redelivered event; already in the note.

        note = self.vault.daily_note_path(day).name
        summary = ", ".join(
            f"{key}: {value}"
            for key, value in entry.fields.items()
            if key != "source" and value
        )
        detail = f"\n{summary}" if summary else ""
        await self._reply(
            event, f"Saved to {note} → RAW\n{entry.kind}: {entry.title}{detail}"
        )

    async def _reply(self, event: dict, text: str) -> None:
        token = event.get("replyToken")
        user_id = (event.get("source") or {}).get("userId")
        try:
            if token:
                await self.client.reply(token, text)
            elif user_id:
                await self.client.push(user_id, text)
        except Exception:  # the capture is already on disk
            logger.exception("Could not reply to LINE event")


def _pending_of(photo: dict) -> list[str]:
    return [key for key in photo.get("pending", "").split(",") if key]


def _parse_amount(text: str) -> tuple[str | None, str]:
    """Split "120 lunch at the market" into ("120", "lunch at the market").

    Returns ``(None, text)`` when the text doesn't start with a
    stand-alone amount.
    """
    match = _AMOUNT_RE.match(text)
    if match is None:
        return None, text
    return match.group(1).replace(",", ""), (match.group(2) or "").strip()


def _to_number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _fmt_amount(value: float) -> str:
    """Whole amounts without decimals, fractional ones with two."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _photo_link(photo: Photo) -> str:
    """An Obsidian embed for a photo in the vault, a path for one outside."""
    return f"![[{photo.vault_relative}]]" if photo.vault_relative else str(photo.path)


def _photo_queue_done(written: bool, skipped: bool) -> str:
    if skipped:
        return "Skipped. That was the last one — all photos handled."
    return (
        "Saved. That was the last one — all photos handled."
        if written
        else "That was the last one — all photos handled."
    )


def _photo_entry(photo: Photo) -> RawEntry:
    digest = hashlib.sha1(str(photo.path.resolve()).encode("utf-8")).hexdigest()[:12]
    link = _photo_link(photo)
    return RawEntry(
        at=photo.taken_at,
        kind="photo",
        title=photo.path.name,
        entry_id=f"photo-{digest}",
        fields={
            "file": link,
            "taken": f"{photo.taken_at:%Y-%m-%d %H:%M}",
            "source": photo.source,
        },
    )


def _entry_id(event: dict) -> str:
    message_id = (event.get("message") or {}).get("id")
    return str(event.get("webhookEventId") or message_id or event.get("timestamp", ""))


def _session_dir(value: str) -> Path:
    """Session state is runtime data — keep it out of the vault."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path
