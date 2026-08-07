# Deploying on a Mac

Written for an always-on Mac mini holding the vault. A Linux box differs
only in the service manager — swap the LaunchDaemon for a systemd unit.

Three things the host must be: awake, holding the vault, and reachable
from the internet. LINE will not deliver to a tailnet address, and a
machine that sleeps returns a timeout and drops the message.

Throughout, `/Users/<you>/projects/satemshi-app` stands for wherever you
cloned the repo. Use the real absolute path — launchd does not expand
`~`.

## 0. Power settings

"Always on" is not the default even for a mini.

```bash
sudo pmset -a sleep 0          # never sleep
sudo pmset -a disksleep 0      # never spin down disks
sudo pmset -a womp 1           # wake on network
sudo pmset -a autorestart 1    # power back on after a cut
pmset -g                       # verify
```

Leave `displaysleep` alone — the screen sleeping is fine.

## 1. Install

```bash
brew install python@3.13
git clone https://github.com/<your-fork>/satemshi-app
cd satemshi-app
python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env && chmod 600 .env
cp config.example.yaml config.yaml
mkdir -p logs
```

Fill in `.env`. Three values matter:

```bash
VAULT_PATH=/Users/<you>/vault
LINE_CHANNEL_SECRET=...            # console → Basic settings
LINE_CHANNEL_ACCESS_TOKEN=...      # console → Messaging API → Issue
```

The access token does not exist until you click **Issue** — creating the
channel does not create it. The secret is 32 characters, the token
around 170; the lengths are a quick sanity check that you copied the
right field from the right tab.

Smoke test. The server runs in the foreground, so use a second terminal
for the health check:

```bash
.venv/bin/python -m satemshi
# other terminal:
curl localhost:8765/healthz        # want "vault_present": true
```

Startup names every setting it took from `.env`. If a key you expect is
missing from that line, see [Troubleshooting](#troubleshooting) — the
message tells you which of the three failure modes you have.

## 2. Run it as a service

Both pieces are in the repo — `run.sh` in the root, and the plist as
`deploy/com.satemshi.capture.plist.template`. One command fills in the
paths and starts it:

```bash
sudo scripts/install-launchdaemon.sh
```

That writes `/Library/LaunchDaemons/com.satemshi.capture.plist` with
this checkout's path and your account name substituted in, then boots
the job. Run it again after editing `run.sh` or the template — it boots
the old job out first, so there is no separate reload procedure.
`scripts/install-launchdaemon.sh --print` shows the plist it would
install without touching anything.

```bash
sudo launchctl print system/com.satemshi.capture    # state = running
tail -f logs/satemshi.err                           # the app logs here
```

Two things the installer will not guess. It wants `sudo` **from your own
account**, because the daemon has to run as the human who owns the vault
— running as root would leave every captured file root-owned. And it
checks `.venv`, `.env` and `run.sh` before writing anything, because
`KeepAlive` turns any one of those being missing into a restart loop
whose only evidence is a line in a log file nobody is tailing yet.

A **LaunchDaemon**, not a LaunchAgent: an agent only runs while you are
logged in, so a reboot would leave the bot down until someone sat at the
machine. `UserName` keeps it running as you, so vault files get the
right ownership.

The plist sets `PATH`, because a daemon does not inherit your shell's
and the vault auto-commit in step 7 needs to find `git`. What it does
**not** hold is your credentials: files in `/Library/LaunchDaemons` are
world-readable, so `run.sh` starts the app in the repo directory and the
app reads its own `.env`, which is 600.

`run.sh` gets that directory from its own location rather than from
whoever invoked it, which is what keeps a clone sitting one level inside
a directory of the same name honest — the copy of `run.sh` you install
is the copy whose `.env` gets read.

For the same reason `run.sh` does not source `.env` either. The app
already reads it, and doing both makes every healthy start log

    <path>/.env has no values filled in.
    Ignored VAULT_PATH, LINE_CHANNEL_SECRET, … already set in the
    environment.

which are the two messages that are supposed to mean something is wrong
(see [Troubleshooting](#troubleshooting)).

## 3. Expose the port

LINE needs public HTTPS. Tailscale Funnel gives a stable hostname with
no domain to buy, no port to forward and no certificate to renew, while
everything else on the box stays tailnet-only.

Install the open-source daemon — **not** the Mac App Store build, which
is sandboxed and gives no usable CLI:

```bash
brew install tailscale
sudo brew services start tailscale
tailscale up
tailscale funnel --bg 8765
tailscale funnel status          # prints your https://<host>.<tailnet>.ts.net
```

`--bg` persists across reboots. Funnel may need enabling for the tailnet
once — the first run prints a link to the admin console.

Verify from **outside** the tailnet (phone on mobile data, not wifi):
`https://<host>.<tailnet>.ts.net/healthz`.

This exposes every path on that port, `/healthz` included. That is a
deliberate, small leak: it reports only whether the vault path resolves.
The webhook is protected by signature verification, not obscurity.

## 4. Point LINE at it

In the console, Messaging API tab:

- **Webhook URL**: `https://<host>.<tailnet>.ts.net/line/webhook`
- **Use webhook**: on
- **Verify** — should go green. A failure here is connectivity, not
  code; go back to step 3.
- **Auto-reply messages**: off. **Greeting messages**: off. Left on,
  they bury the bot's replies in LINE's canned ones.

## 5. Lock it to you

Add the bot as a friend and send `whoami`. Put the id it returns in
`config.yaml`:

```yaml
line_bot:
  allowed_user_ids: ["Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"]
```

Restart the service to pick it up — `config.yaml` is read once, at
startup:

```bash
sudo launchctl kickstart -k system/com.satemshi.capture
```

**Do this before capturing anything real** — with
the list empty, anyone who finds the URL can write into your vault, and
the URL is public by construction now.

## 6. First capture

Send `note first capture`. Expect `Saved to YYYY-MM-DD.md → RAW`, and
the file to exist under `Daily Notes/`. Silence means the event either
never arrived or failed on arrival; `logs/satemshi.err` distinguishes
the two.

## 7. Give the vault a history

Captures now live on exactly one disk, with no way back from a bad edit.
Auto-commit fixes both halves of that.

The vault has to be a git repo first — the app will not create one:

```bash
cd /Users/<you>/vault
git init
git add -A && git commit -m "vault before satemshi"
git remote add origin git@github.com:<you>/vault.git   # private repo!
git push -u origin HEAD
```

Then in `config.yaml`:

```yaml
vault_git:
  enabled: true
  coalesce_seconds: 300
  auto_push: true
```

Restart the service (`sudo launchctl kickstart -k
system/com.satemshi.capture`). Startup now says what it will do, so a
misconfiguration surfaces at boot rather than five minutes into the
first capture:

    Vault auto-commit is on: committing and pushing /Users/<you>/vault every 300s.

`coalesce_seconds` is a window, not a delay on everything. The first
change opens it, every change landing inside joins the same commit, and
the commit is made when it closes — so a capture and the four answers
that follow it are one commit rather than five, and nothing waits longer
than the window. Stopping the service closes an open window early and
commits it, rather than losing it.

`auto_push` needs credentials that work with nobody at the keyboard: an
SSH key with no passphrase, or one already in the keychain. Git is run
with prompting turned off, so a remote that wants a password is a logged
failure and a commit that stays local — never a service hung forever on
a prompt no one can see. Commits are unsigned for the same reason: a GPG
pinentry has nobody to ask.

None of this can lose a capture. The note is written and the reply sent
before git is involved, and every failure leaves the change in the
working tree for the next commit to sweep up. An unfinished merge or
rebase in the vault pauses auto-commit until you finish it — captures
keep arriving, they just wait rather than being committed on top of a
conflict you were in the middle of resolving.

## 8. Photos

Point a sync client (Syncthing, or the desktop client for iCloud or
Google Photos) at a folder on this machine, and list it in
`config.yaml`:

```yaml
photos:
  source_dirs: ["/Users/<you>/phone-sync"]
```

Then `photos` in the chat sweeps that folder for pictures taken today,
records them, and asks about each. See [line-bot.md](line-bot.md) for
why the camera roll has to come to the machine rather than the app
reaching for it.

## Troubleshooting

**Startup says something about `.env`.** Three distinct messages, three
distinct causes:

| message | meaning |
| --- | --- |
| `No .env at <path>` | wrong directory, or never copied from the template |
| `<path> has no values filled in` | the file is still the template |
| `Ignored X, Y … already set in the environment` | the file is right and being **overridden** by shell exports |

The third is the confusing one, because the file looks correct. A real
environment variable deliberately wins over `.env`. To test what the
service will actually see, unset them for one command:

```bash
env -u VAULT_PATH -u LINE_CHANNEL_SECRET -u LINE_CHANNEL_ACCESS_TOKEN \
  .venv/bin/python -m satemshi
```

Check `~/.zshrc` and friends if they keep coming back. Harmless for the
service — LaunchDaemons do not read your shell config — but it makes
by-hand runs ambiguous.

**Two directories, two `.env` files.** If the clone sits one level
inside a directory of the same name, it is easy to edit one `.env` and
run the other. The startup line names the full path it read; trust that
over which directory you think you are in.

**Captures arrive but the vault is never committed.** Read the
auto-commit line at startup first; it reports the state it found. Then,
in order of how often it is the cause:

| symptom in `logs/satemshi.err` | cause |
| --- | --- |
| `Vault auto-commit is off` | `vault_git.enabled` is not set in `config.yaml` |
| `is not a git repository` | step 7's `git init` was never run |
| `git is not on PATH` | the plist was hand-written without the `PATH` key |
| `no 'origin' remote` | `auto_push` is on with nothing to push to |
| `unfinished merge` / `rebase` | finish it in the vault; captures resume after |

A push that fails on credentials logs the git error verbatim. Commits
still happen — only the copy off the machine is missing, so it is worth
fixing rather than ignoring.

**`Operation not permitted` writing to the vault.** macOS TCC blocks
background daemons from `~/Documents`, `~/Desktop`, `~/Downloads` and
iCloud Drive. Move the vault somewhere neutral like `~/vault`. The same
applies to the photo sync folder.

**Everything stops after a power cut.** With FileVault on, the machine
will not reach a state where daemons run until someone unlocks it.
Either turn FileVault off on this machine or accept a manual unlock;
`autorestart 1` does not help here.
