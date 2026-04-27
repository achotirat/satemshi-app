# Satemshi App

A personal assistant + second brain that **captures** moments during the day (links, mood, people met, priorities, to-buy list) and **ingests** them into an Obsidian vault as a structured wiki — using a swappable LLM backend (local Ollama, Claude API, or other providers) per task.

> Status: **Phase 0 — repo skeleton.** No application code yet. See `docs/foundation.md` (forthcoming) for the architecture blueprint.

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
Webapp (FastAPI + HTMX) ──▶ Capture API ──▶ Vault Writer ──▶ Daily Notes/<date>.md
                                                                    │
                                                       cron @ 02:00 │
                                                                    ▼
                                                       Ingest Pipeline ──▶ Wiki/
                                                              │
                                                              └──▶ LLM Gateway (Ollama / Claude / ...)
```

See `docs/foundation.md` for the full picture (forthcoming).

## Status & phasing

- **Phase 0 (current):** repo skeleton, license, secret-scan hooks, vault conventions documented.
- **Phase 1:** capture skeleton (links, priorities, to-buy list — no LLM yet).
- **Phase 2:** LLM-powered captures (mood check-in question generator, people-met entity suggestions).
- **Phase 3:** nightly ingest pipeline (wiki summary + entity extraction + index updates).

Each phase has its own design spec and implementation plan.

## Getting started (when there is code)

```bash
git clone https://github.com/<your-fork>/satemshi-app
cd satemshi-app
cp .env.example .env           # edit with your vault path, LLM keys
cp config.example.yaml config.yaml
# (instructions to run will appear in Phase 1)
```

## License

MIT. See [LICENSE](LICENSE).
