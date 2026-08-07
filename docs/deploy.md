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

launchd has no `EnvironmentFile`, so a wrapper sources `.env` rather
than pasting secrets into a plist — plists in `/Library/LaunchDaemons`
are world-readable; your `.env` is 600.

`run.sh` in the repo root:

```bash
#!/bin/bash
cd "$(dirname "$0")"
set -a; . ./.env; set +a
exec .venv/bin/python -m satemshi
```

```bash
chmod +x run.sh
```

`/Library/LaunchDaemons/com.satemshi.capture.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>              <string>com.satemshi.capture</string>
    <key>ProgramArguments</key>
      <array><string>/Users/<you>/projects/satemshi-app/run.sh</string></array>
    <key>WorkingDirectory</key>   <string>/Users/<you>/projects/satemshi-app</string>
    <key>UserName</key>           <string><you></string>
    <key>RunAtLoad</key>          <true/>
    <key>KeepAlive</key>          <true/>
    <key>StandardOutPath</key>
      <string>/Users/<you>/projects/satemshi-app/logs/satemshi.log</string>
    <key>StandardErrorPath</key>
      <string>/Users/<you>/projects/satemshi-app/logs/satemshi.err</string>
</dict>
</plist>
```

```bash
sudo chown root:wheel /Library/LaunchDaemons/com.satemshi.capture.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.satemshi.capture.plist
sudo launchctl print system/com.satemshi.capture    # state = running
tail -f logs/satemshi.err                           # the app logs here
```

Reload after editing either file:

```bash
sudo launchctl bootout system/com.satemshi.capture
sudo launchctl bootstrap system /Library/LaunchDaemons/com.satemshi.capture.plist
```

A **LaunchDaemon**, not a LaunchAgent: an agent only runs while you are
logged in, so a reboot would leave the bot down until someone sat at the
machine. `UserName` keeps it running as you, so vault files get the
right ownership.

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

Restart the service. **Do this before capturing anything real** — with
the list empty, anyone who finds the URL can write into your vault, and
the URL is public by construction now.

## 6. First capture

Send `note first capture`. Expect `Saved to YYYY-MM-DD.md → RAW`, and
the file to exist under `Daily Notes/`. Silence means the event either
never arrived or failed on arrival; `logs/satemshi.err` distinguishes
the two.

## 7. Photos

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

**`Operation not permitted` writing to the vault.** macOS TCC blocks
background daemons from `~/Documents`, `~/Desktop`, `~/Downloads` and
iCloud Drive. Move the vault somewhere neutral like `~/vault`. The same
applies to the photo sync folder.

**Everything stops after a power cut.** With FileVault on, the machine
will not reach a state where daemons run until someone unlocks it.
Either turn FileVault off on this machine or accept a manual unlock;
`autorestart 1` does not help here.
