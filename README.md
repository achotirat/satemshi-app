# Satemshi App

[![CI](https://github.com/achotirat/satemshi-app/actions/workflows/ci.yml/badge.svg)](https://github.com/achotirat/satemshi-app/actions/workflows/ci.yml)

A personal assistant + second brain that **captures** moments during the day (links, mood, people met, priorities, to-buy list) and **ingests** them into an Obsidian vault as a structured wiki — using a swappable LLM backend (local Ollama, Claude API, or other providers) per task.

> Status: **Phase 1 — first capture channel.** A LINE Bot captures today's events and photos into the vault's RAW zone. See [`docs/line-bot.md`](docs/line-bot.md).

## Why this exists

Keeping a personal knowledge base alive requires constant capture and constant curation. Most note-taking tools do one well and the other badly. Satemshi App splits the problem:

- **Capture** is mobile-first, instant, and hits a daily note in a known location.
- **Ingestion** is a nightly batch that an LLM runs over yesterday's notes — it summarizes, links entities, and updates the wiki.
- **Curation** is the user's job, in Obsidian, on top of a wiki the agent keeps tidy.

The framework is sharable. The data is yours.

## Design principles

- **Vault-first.** The Obsidian vault is the database. There is no other store.
- **Two-zone Daily Notes.** The webapp owns a clearly-marked region of each daily note. Everything else is the user's freehand journal — never touched.
- **Pluggable LLM.** Every LLM call goes through a gateway with a `task → provider+model` map. Run everything on a local Ollama, or reach for Claude when a task needs more capability.
- **Hard separation of code and data.** This repo (public, MIT) contains no personal data. The vault lives in a separate, private repo.
- **Tailscale-only access.** No public exposure. Auth is the network.

## Architecture (planned)

```
LINE Bot ──────────────┐
                       ├──▶ Capture API ──▶ Vault Writer ──▶ Daily Notes/<date>.md
Webapp (FastAPI + HTMX)┘                          │                    │
                                                  ▼                    │
                                        Attachments/<yyyy>/<mm>/       │
                                                                       │
                                                          cron @ 02:00 │
                                                                       ▼
                                                          Ingest Pipeline ──▶ Wiki/
                                                                 │
                                                                 └──▶ LLM Gateway (Ollama / Claude / ...)
```

See `docs/foundation.md` for the full picture (forthcoming).

## Status & phasing

- **Phase 0 (done):** repo skeleton, license, secret-scan hooks, vault conventions documented.
- **Phase 1 (current):** capture skeleton, no LLM yet. The [LINE Bot](docs/line-bot.md) captures events and photos into the daily note's RAW zone; links, priorities and the to-buy list follow.
- **Phase 2:** LLM-powered captures (mood check-in question generator, people-met entity suggestions).
- **Phase 3:** nightly ingest pipeline (wiki summary + entity extraction + index updates).

Each phase has its own design spec and implementation plan.

## Getting started

```bash
git clone https://github.com/<your-fork>/satemshi-app
cd satemshi-app
cp .env.example .env           # vault path + LINE channel credentials
cp config.example.yaml config.yaml
pip install -e ".[dev]"
pytest
python -m satemshi             # serves /line/webhook and /healthz
```

Then point your LINE channel's webhook at `https://<your-host>/line/webhook`
— see [`docs/line-bot.md`](docs/line-bot.md) for the full setup, the
command list, and how photos are found.

## Development

Three checks run on every push and pull request, and all three are worth
running before you push:

```bash
pytest                      # the suite
ruff check satemshi tests   # lint
pre-commit run --all-files  # personal-data scan, hygiene
```

Install the hooks once (`pip install pre-commit && pre-commit install`)
and the third runs itself on every commit. It is the one that matters
most here: this repo is public, the vault it writes to is not, and the
hooks exist to keep a personal path or an API key from crossing that
line.

One wrinkle worth knowing: the gitleaks hook runs `gitleaks protect
--staged`, so it scans **what you are about to commit**. That is what
you want from a commit hook, and it means `pre-commit run --all-files`
does *not* secret-scan the repo — with nothing staged there is nothing
for it to look at. To scan by hand:

```bash
gitleaks detect --source . --redact   # every commit in history
```

CI does exactly that, in addition to running the hooks over every file,
so a pull request authored without the hooks installed is still caught.

## License

MIT. See [LICENSE](LICENSE).
