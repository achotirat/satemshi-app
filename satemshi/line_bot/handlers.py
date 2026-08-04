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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..models import RawEntry
from ..photos import Photo, PhotoStore, exif_datetime, suffix_for_content_type
from ..vault import VaultWriter
from .client import MessagingClient
from .sessions import Session, SessionStore

logger = logging.getLogger(__name__)

SKIP_TOKENS = {"-", "skip", "ข้าม"}

HELP_TEXT = (
    "Send me anything about today and it lands in your vault's RAW zone.\n\n"
    "• any text — starts an event capture, then I ask a few follow-ups\n"
    "• a photo — saved to the vault and recorded in RAW\n"
    "• a location — recorded, or used as the answer to “where”\n\n"
    "Commands:\n"
    "• today — read back today's RAW zone\n"
    "• photos — find today's photos and record them\n"
    "• note <text> — capture verbatim, no questions\n"
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
                await self._reply(event, self._sweep_photos())
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
                else:
                    await self._finalise(event, session)
                return

        if session is not None:
            await self._answer_slot(event, session, body)
            return

        await self._start_capture(
            event, user_id, argument if command == "event" else body
        )

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

        if session is not None and session.slot_index < len(slots):
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

    def _sweep_photos(self) -> str:
        today = self._now().date()
        found = self.photos.find_for_day(today)
        if not found:
            roots = len(self.config.photos.source_dirs) + 1
            return (
                f"No photos found for {today.isoformat()} across {roots} "
                "location(s). Send one here and I'll file it."
            )

        added = 0
        for photo in found:
            if self.vault.append(_photo_entry(photo), day=today):
                added += 1

        listing = "\n".join(
            f"• {photo.taken_at:%H:%M} — {photo.display} ({photo.source})"
            for photo in found
        )
        return (
            f"{len(found)} photo(s) for {today.isoformat()}, "
            f"{added} newly recorded in RAW:\n\n{listing}"
        )

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


def _photo_entry(photo: Photo) -> RawEntry:
    digest = hashlib.sha1(str(photo.path.resolve()).encode("utf-8")).hexdigest()[:12]
    link = f"![[{photo.vault_relative}]]" if photo.vault_relative else str(photo.path)
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
