# LINE Bot capture channel

The bot is a capture surface for the day you are actually having: you
message it as things happen, and each message lands in the **RAW zone**
of today's daily note, verbatim. Nothing is summarised, rewritten or sent
to an LLM here — that is the nightly ingest's job. RAW is the raw
material it works from.

It is also how the daily note gets *written*. `journal` puts your own
prose in the note's **JOURNAL zone** — no questions, no fields, the text
as you typed it — so writing the day up from the phone is the same
channel as capturing it. See [Writing the day itself](#writing-the-day-itself).

## What lands where

```
VAULT_PATH/
├── Daily Notes/
│   └── 2026-08-04.md          ← two marked zones; the rest is yours
└── Attachments/2026/08/       ← photos sent to the bot
```

A daily note the bot has written to:

```markdown
# Wednesday                          ← yours, never touched

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

## Journal

<!-- journal:start -->
**21:06** — Rain finally broke the heat, so I walked to the market
before it got busy. ^journal-e2

**21:14** — Ended the day on the balcony with Nok. Good day. ^journal-e5
<!-- journal:end -->

## Evening                           ← also yours, also never touched
```

In RAW, fields are Dataview inline fields (`key:: value`) so the ingest
pipeline can read a capture back without parsing prose. The JOURNAL zone
has no fields: it is prose, and the ingest quotes it rather than parsing
it. Both carry a block id — `^raw-…`, `^journal-…` — which is the entry's
identity: LINE redelivers webhook events, and a redelivery finds its own
id already present and writes nothing. The journal's ids also make a line
embeddable from the wiki (`![[2026-08-04#^journal-e2]]`).

Only the bytes between a zone's markers are ever modified. If the markers
are missing the bot appends a fresh zone at the end of the note; if they
are unbalanced it refuses to write, and tells you in the chat rather than
guessing where the zone ends. The four markers must all differ from each
other — a config that reuses one is rejected at startup, because zone
boundaries are found by substring search and a shared marker would have
one zone's write land inside the other's.

Text you send cannot forge a marker either: a message containing
`<!-- journal:end -->` is written with the literal broken by a zero-width
space, so it reads as typed and closes nothing.

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
| `note <text>` | captures verbatim into RAW, no questions asked |
| `journal <text>` | writes that line into today's JOURNAL zone (`บันทึก` works too) |
| `journal` | opens journal mode: every message goes in until `done` |
| `today` | reads back today's captures and journal |
| `photos` | finds today's photos, records them, then asks about each (below) |
| `next` | skips to the next photo in the queue |
| `slip` (to a photo question) | records that photo as an expense: amount → category → what for |
| `spent <amount> <what>` | records an expense without a photo (`จ่าย` works too) |
| `expenses` | today's spending, totalled by category |
| `whoami` | your LINE user id, for `allowed_user_ids` |
| `help` | the command list |

While a capture is in progress a bare word is an **answer**, not a
command — "today" is a perfectly good answer to "when did it happen?".
Prefix a command with `/` (`/today`, `/photos`) to run it mid-capture.
`done` and `cancel` always act as commands.

## Writing the day itself

A capture answers *what happened*. The daily note also wants the other
thing — how the day went, in sentences — and that is what `journal` is
for. There are no follow-up questions and no fields: the message is the
entry.

One line at a time:

```
you → journal Slept badly, up before the heat.
bot ← Saved to 2026-08-04.md → JOURNAL
      06:40 — Slept badly, up before the heat.
```

Writing the day up in the evening is not one message, though, so
`journal` on its own opens the zone and keeps it open:

```
you → journal
bot ← Journal open for 2026-08-04.md — every message goes in as you
      typed it, no questions.
      "done" closes it; slash a command ("/today", "/note …") to run it
      without closing.
you → Up early. The rain finally broke the heat, so I walked to the
      market before it got busy.
bot ← In the journal, 21:06 — 1 line(s) today.
you → Spent the afternoon on the deploy doc.
      Still not happy with the Tailscale section — rewrite tomorrow.
bot ← In the journal, 21:09 — 2 line(s) today.
you → done
bot ← Journal closed — 2 line(s) in today's note. Everything you sent
      is already in it; edit it in Obsidian.
```

Each line is written the moment it arrives, so nothing is held hostage by
a session you never close — and `cancel` says as much rather than
claiming the lines were dropped. Newlines inside a message survive, so a
message sent as three lines stays three lines. A line with a typo is
fixed in Obsidian; the bot only ever appends.

Journal mode is a session like any other, with the consequences that
implies. A bare word is journalled rather than run as a command, so
`/today` and `/note` need the slash — and `/journal <text>` writes one
line without disturbing anything, which also works mid-capture. `spent`
and `photos`' questions need a session of their own and are refused while
the journal is open — `done` first, then record the expense. And the
session expires after
`session_ttl_seconds`: come back to the chat an hour later and the next
message starts an event capture instead, so re-open the journal after a
long gap.

Photos are unaffected by the mode: an image always goes to the vault and
into RAW, where the `photos` questions can find it. Reference it from a
journal line in Obsidian if the story needs the picture.

`today` reads both zones back, captures first, journal after — block ids
stripped, since those are machinery:

```
2026-08-04 — 2 capture(s):

- **12:47** `event` Lunch with the Krabi supplier ^raw-e1
…

Journal — 2 line(s):

**21:06** — Up early. The rain finally broke the heat …
```

The zone is configured under `journal:` in `config.yaml`: its heading,
its markers, and `timestamps: false` for a journal that reads as
continuous prose instead of a log. Put the markers into your daily-note
template to fix where the zone sits in the note — otherwise the bot
appends it at the end the first time you write.

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

## Recording expenses

Thai banking apps save a slip to the gallery for every transfer and
PromptPay payment — which means the photo sweep already finds the day's
payments. When the queue reaches a slip, answer **`slip`** instead of
the first question and the bot switches to expense questions for that
photo:

```
bot ← Screenshot_KBank_1240.jpg (12:40) [2 left]
      Where was this taken?
you → slip
bot ← Recording it as an expense.
      How much was it? (just the number, e.g. 120 or 1,250.50)
you → 350
bot ← Category? Reply a number or a name:
      1 food  2 transport  3 groceries  4 household  5 health  6 fun  7 bills  8 other
you → 3
bot ← What was it for?
you → weekly market run
```

The answers land on the slip's own RAW entry, next to the file link —
one entry ties together the image, the amount and the category:

```markdown
- **12:40** `photo` Screenshot_KBank_1240.jpg ^raw-photo-9c2f11ab0d44
    - file:: /home/user/phone-sync/Screenshot_KBank_1240.jpg
    - taken:: 2026-08-05 12:40
    - source:: scan
    - amount:: 350
    - category:: groceries
    - what:: weekly market run
```

Cash and anything without a slip goes in directly with **`spent`** (or
Thai **`จ่าย`**):

```
you → spent 120 lunch at the market
bot ← Category? Reply a number or a name: …
you → 1
bot ← Saved to 2026-08-05.md → RAW
```

The amount is picked out of the message ("120", "1,250.50", "฿120",
"120 baht" all work), so usually only the category question remains;
`spent` alone asks for amount, category and description in turn.

**`expenses`** reads the day back by category:

```
2026-08-05 — 3 expense(s), total 850
bills: 380 (1)
groceries: 350 (1)
food: 120 (1)

• 07:20 — 120 food — lunch at the market
• 12:40 — 350 groceries — weekly market run
…
```

(Categories are listed biggest first; the detail lines follow the
order they sit in the note.)

The categories are `line_bot.expense_categories` in config — edit the
list to match how you actually budget; a reply that matches nothing is
kept verbatim as a free-form category, and `[]` turns the category
question off. Amounts are stored as plain `amount::` fields, so the
nightly ingest (Phase 3) can roll them into weekly or monthly ledgers
per category without re-parsing anything.

### Getting the camera roll within reach

The bot cannot read your phone's Photos app. A LINE bot only ever sees
what is sent to it in the chat — the Messaging API has no access to the
device, and iOS and Android do not hand the camera roll to a server.
Google's Photos Library API is not a way around this either: since 2025
it only returns media the calling app itself created, and anything else
requires the user to pick photos by hand in the Picker.

The same goes for assistant apps on the machine (Claude's Cowork desktop
app and the like): they run on the desktop, not the phone, so they can
only see a folder the phone has already synced — which is exactly what
`photos.source_dirs` reads. The camera roll has to come to a directory
the server can see, and then the sweep does the rest.

**On Android, Syncthing is the clean path** — camera roll to server
folder, automatically, EXIF intact, no cloud in between:

1. Install [Syncthing-Fork](https://github.com/Catfriend1/syncthing-android)
   on the phone (the actively maintained Android build) and Syncthing on
   the machine running satemshi.
2. On the phone, share the `DCIM/Camera` folder; on the server, accept
   it into e.g. `~/phone-sync/camera`, and mark the server side
   "Receive Only" so nothing can touch the photos on the phone.
3. Point the config at it:

   ```yaml
   photos:
     source_dirs: ["~/phone-sync/camera"]
   ```

From then on, sending `photos` to the bot any evening lists what you
shot today and starts the questions. Alternatives: Nextcloud auto-upload
works the same way; on iOS, a Shortcuts automation can send the day's
photos to the bot over LINE instead; and hand-sending the ones that
matter in the chat always works.

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
