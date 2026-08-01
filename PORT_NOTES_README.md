# OldPrefactor / GrazyBot Port Notes

## Purpose

Modernize `OldPrefactor.py` into modular Pycord cogs and expose a read-only HTTPS API so Base44 (`misclickerz-hub.base44.app`) can act as a clan dashboard.

## Local paths

| Item | Path |
|------|------|
| Bot root | `C:\Users\macfl\OneDrive\Desktop\PROJECT GRAZYBOT` |
| Monolith fallback | `...\OldPrefactor.py` |
| Modular entry | `...\main.py` |
| Hub API | `C:\Users\macfl\OneDrive\Desktop\Grazybot-API` |

## Architecture

```
Discord (members/admins)
        │
        ▼
GrazyBot main.py  ──asyncpg──►  Postgres (source of truth)
        │ optional push
        ▼
Base44 /api/ingest (if BASE44_HUB_URL set)

Base44 dashboard ──Bearer──► Grazybot-API ──asyncpg──► same Postgres
```

## Bot boot (modular)

**Requires a working Python 3.12+ install.** If venvs say `No Python at ...Python312...`, reinstall Python 3.12 and recreate the venv:

```powershell
cd "C:\Users\macfl\OneDrive\Desktop\PROJECT GRAZYBOT"
# if venv is broken:
# py -3.12 -m venv .venv
# .\.venv\Scripts\Activate
# pip install -r requirements.txt

.\venv\Scripts\Activate   # or .\.venv after recreate
python main.py
```

Uses **Pycord** (`py-cord`), not stock `discord.py`.

### Loaded cogs

`admin`, `bingo`, `events`, `ge`, `giveaway`, `osrs`, `pb`, `points`, `pointstore`, `pvm`, `raffle`, `sotw`, `tasks`

### Env

Copy `.env.example` → `.env`. Never commit `.env`.

Optional hub push:

```
BASE44_HUB_URL=https://misclickerz-hub.base44.app
BASE44_HUB_TOKEN=...
```

## Hub API

```powershell
cd "C:\Users\macfl\OneDrive\Desktop\Grazybot-API"
pip install -r requirements.txt
# set DATABASE_URL (same as bot) + HUB_API_TOKEN
uvicorn main:app --reload --port 8000
```

### Public

| Method | Path | Notes |
|--------|------|--------|
| GET | `/` | Health |
| GET | `/status` | Health + DB ping |

### Bearer-protected (`Authorization: Bearer <HUB_API_TOKEN>`)

| Method | Path |
|--------|------|
| GET | `/api/sotw/active` |
| GET | `/api/points/{discord_id}` |
| GET | `/api/pb/{boss}` |
| GET | `/api/events/upcoming` |

### Base44 builder prompt (paste into Base44)

```
Build a 3-page clan hub. On load, call these APIs with header
Authorization: Bearer <HUB_API_TOKEN> (store token in Base44 env, never hardcode):

GET {API_BASE}/api/sotw/active → Home banner + top 10
GET {API_BASE}/api/events/upcoming → Events page
GET {API_BASE}/api/points/{discord_id} → member points (after Discord login)
GET {API_BASE}/api/pb/{boss} → PB leaderboard page

CORS origin: https://misclickerz-hub.base44.app
API is read-only. Do not write to Postgres.
```

Deploy API with `render.yaml` (set `DATABASE_URL`, `HUB_API_TOKEN` in Render dashboard).

## Secrets policy

**Share:** code, `.env.example`, this README, CHANGELOG  
**Never share:** real `.env`, tokens, DB passwords, Supabase service keys

## Changelog

See `prefactor-port/CHANGELOG.md`.

## Fallback

If modular bot fails: `python OldPrefactor.py` (Gemini model updated to `gemini-2.0-flash`).
