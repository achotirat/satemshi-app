# LINE Bot capture channel

The bot is a capture surface for the day you are actually having: you
message it as things happen, and each message lands in the **RAW zone**
of today's daily note, verbatim. Nothing is summarised, rewritten or sent
to an LLM here — that is the nightly ingest's job. RAW is the raw
material it works from.

## What lands where

```
VAULT_PATH/
├── Daily Notes/
│   └── 2026-08-04.md          ← captures append inside the RAW markers
└── Attachments/2026/08/       ← photos sent to the bot
```

A daily note the bot has written to:

```markdown
# Wednesday                          ← your journal, never touched

## RAW

<!-- raw:start -->
- **12:47** `event` Lunch with the Krabi supplier ^raw-e1
    - when:: 12:30
    - where:: Ao Nang
    - who:: Nok, Bank
    - notes:: great mango sticky rice
    - source:: line/U1234
- **08:12** `photo` IMG_2201.jpg ^raw-photo-41c590b92305
    - file:: /home/user/phone-sync/IMG_2201.jpg
    - taken:: 2026-08-04 08:12
    - source:: scan
<!-- raw:end -->

## Evening                           ← also yours, also never touched
```

Fields are Dataview inline fields (`key:: value`) so the ingest pipeline
can read a capture back without parsing prose. The `^raw-…` block id is
the capture's identity: LINE redelivers webhook events, and a redelivery
finds its own id already present and writes nothing.

Only the bytes between `<!-- raw:start -->` and `<!-- raw:end -->` are
ever modified. If the markers are missing the bot appends a fresh zone at
the end of the note; if they are unbalanced it refuses to write and says
so, rather than guessing where the zone ends.

## Talking to it

Send anything about today and the bot starts an **event capture**, then
asks the follow-ups configured in `line_bot.event_slots`, one per
message:

```
you → Lunch with the Krabi supplier
bot ← Got it: "Lunch with the Krabi supplier".
      When did it happen? ("-" to skip, "done" to finish, "cancel" to drop)
you → 12:30
bot ← Where was it?
…
bot ← Saved to 2026-08-04.md → RAW
```

The note is written once, at the end — a half-answered capture leaves
nothing behind. Answers live in `sessions/` until then, so restarting the
server mid-conversation does not lose them; a capture abandoned for
`session_ttl_seconds` (30 min by default) expires, so tomorrow's first
message is never read as an answer to yesterday's question.

| Send | What happens |
| --- | --- |
| any text | starts an event capture with follow-up questions |
| `-` / `skip` | leaves the current question unanswered |
| `done` | writes the capture with whatever has been answered |
| `cancel` | throws the draft away, writes nothing |
| a photo | downloaded, filed in the vault, recorded in RAW |
| a location | recorded — or used as the answer to "where" mid-capture |
| `note <text>` | captures verbatim, no questions asked |
| `today` | reads back today's RAW zone |
| `photos` | finds today's photos, records them, then asks about each (below) |
| `next` | skips to the next photo in the queue |
| `whoami` | your LINE user id, for `allowed_user_ids` |
| `help` | the command list |

While a capture is in progress a bare word is an **answer**, not a
command — "today" is a perfectly good answer to "when did it happen?".
Prefix a command with `/` (`/today`, `/photos`) to run it mid-capture.
`done` and `cancel` always act as commands.

## Finding today's photos — and asking about them

`photos` sweeps two places and records anything not already in RAW:

- `Attachments/{yyyy}/{mm}` in the vault — the photos you sent the bot;
- every directory in `photos.source_dirs` — a phone sync folder, a
  camera import directory. These are read-only: the sweep records what
  it finds, it never moves or edits them.

"Taken today" means EXIF `DateTimeOriginal` where there is one, and the
file's modification time otherwise — so a screenshot or a PNG still lands
on the right day. EXIF is parsed directly from the JPEG, so the capture
path needs no imaging library. The sweep is idempotent: run it at noon
and again at midnight and the second run only records what is new.

A photo file only tells you *when* — never *where* it was or *what* it
was. So once the sweep has recorded them, the bot walks through the
photos that have no answers yet and asks the `photo_slots` questions
about each one:

```
you → photos
bot ← 3 photo(s) for 2026-08-04, 3 newly recorded in RAW:
      • 07:12 — IMG_2201.jpg (scan)
      • 12:40 — IMG_2202.jpg (scan)
      • 18:03 — IMG_2203.jpg (scan)

      3 still unanswered — let's go through them.
      ("-" skips a question, "next" skips the photo, "done" stops)

      IMG_2201.jpg (07:12) [3 left]
      Where was this taken?
you → Ao Nang beach
bot ← What is it — and who's in it?
you → sunrise, walking with Nok
bot ← Saved.

      IMG_2202.jpg (12:40) [2 left]
      Where was this taken?
```

Answers are added to the photo's **existing** RAW entry rather than
written as a new one, so a photo stays one entry that grows:

```markdown
- **07:12** `photo` IMG_2201.jpg ^raw-photo-41c590b92305
    - file:: /home/user/phone-sync/IMG_2201.jpg
    - taken:: 2026-08-04 07:12
    - source:: scan
    - where:: Ao Nang beach          ← added when you answered
    - what:: sunrise, walking with Nok
```

Answers are written as each photo finishes, so stopping halfway keeps
what you already said. `done` stops the queue; running `photos` again
picks up only the ones still unanswered. Dropping a LINE location pin
answers the question on the table — handy for "where was this taken?".
Set `photo_slots: []` to turn the questions off and have the sweep only
list what it found.

Note that mid-queue a bare word is an answer, so use `/photos` or
`/today` if you want the command while questions are open.

### Getting the camera roll within reach

The bot cannot read your phone's Photos app. A LINE bot only ever sees
what is sent to it in the chat — the Messaging API has no access to the
device, and iOS and Android do not hand the camera roll to a server.
Google's Photos Library API is not a way around this either: since 2025
it only returns media the calling app itself created, and anything else
requires the user to pick photos by hand in the Picker.

So the camera roll has to come to a directory the server can see, and
then `photos.source_dirs` does the rest:

- **A sync client** — Syncthing, Nextcloud, or the desktop client for
  iCloud or Google Photos, pointed at a folder on the machine running
  the app. Set and forget, and the sweep sees each day's photos with
  their EXIF intact.
- **An iOS Shortcuts automation** — "at 21:00, take today's photos and
  send them to <the bot>". Photos sent this way arrive over LINE and are
  filed in the vault directly.
- **By hand** — send the ones that matter in the chat as the day goes.

The first is the one worth setting up: it needs no daily action, and the
questions above are what turn a folder of files into something the wiki
can use.

## Setting it up

1. Create a **Messaging API** channel in the
   [LINE Developers console](https://developers.line.biz/console/).
2. Put its channel secret and a long-lived channel access token in
   `.env` as `LINE_CHANNEL_SECRET` and `LINE_CHANNEL_ACCESS_TOKEN`.
3. Copy `config.example.yaml` to `config.yaml` and adjust `timezone`,
   `event_slots` and `photos.source_dirs`.
4. Run the server:

   ```bash
   pip install -e .
   python -m satemshi          # honours APP_BIND_HOST / APP_BIND_PORT
   ```

5. Point the channel's webhook URL at `https://<your-host>/line/webhook`
   and disable the default auto-reply messages.
6. Add the bot as a friend, send `whoami`, and put the id it returns in
   `line_bot.allowed_user_ids`.

LINE requires a public HTTPS webhook, so this endpoint is the one part
of the app that cannot live purely on the tailnet. Two things keep it
honest: every request must carry a valid `X-Line-Signature` (HMAC-SHA256
of the raw body with the channel secret — an unsigned or wrongly signed
request gets a 401 and is never parsed), and `allowed_user_ids` decides
who may write to the vault. Leaving that list empty means anyone who can
reach the webhook can capture into your notes; set it.

`GET /healthz` reports whether the vault path is visible, which is the
usual thing to check first when captures stop appearing.

## Design notes

- **The vault write comes first.** Every branch writes to disk before it
  replies, and a failed LINE API call is logged rather than raised — a
  capture that reached the vault is not lost because a reply token
  expired.
- **A bad event does not stall the batch.** LINE delivers events in
  batches and retries anything that is not a 200, so each event is
  handled independently and the webhook acknowledges once the batch has
  been processed.
- **Writes are atomic.** Notes are written to a temporary file in the
  same directory and `os.replace`d into position, so a crash mid-write
  cannot truncate a daily note.
