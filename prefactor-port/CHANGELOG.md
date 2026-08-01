# Prefactor Port Changelog

## 2026-07-13 — Boot fix + hub API MVP

### Modular bot (`PROJECT GRAZYBOT`)

- Fixed all `from bot.*` / `from cog.*` imports to flat project-root imports
- Renamed unimportable `cogs/tasks..py` → `cogs/tasks.py`
- Rewrote `main.py` as Pycord `discord.Bot` entry with:
  - `load_cogs()` for known extensions
  - DB pool + item mapping + persistent views on ready
  - aiohttp health server on `PORT` (default 10000)
  - Pycord `sync_commands()` (no discord.py `bot.tree`)
- Flattened `requirements.txt` (was a nested directory); added `py-cord`, `google-generativeai`, `Pillow`
- Made `SOTW_ROLE_ID` optional (was blocking boot when unset)
- **SOTW:** insert into `active_competitions` after WOM create
- **Tasks:** full `periodic_event_reminder` + midway SOTW pings + giveaway entry updates
- **Bingo:** full PIL board image from OldPrefactor
- **Hub push:** `helpers/hub.py` + hooks on SOTW start, raffle draw, PB log
- **OldPrefactor:** Gemini model `gemini-1.0-pro` → `gemini-2.0-flash`

### Grazybot-API

- asyncpg pool on `DATABASE_URL` (bot tables = source of truth)
- Bearer auth via `HUB_API_TOKEN` / `BASE44_HUB_TOKEN`
- Routes: `GET /`, `/status`, `/api/sotw/active`, `/api/points/{id}`, `/api/pb/{boss}`, `/api/events/upcoming`
- CORS includes `https://misclickerz-hub.base44.app`
- Filled `render.yaml`, real `test_api.py`, updated `.env.example`

### Still optional / not loaded

- Nested `cogs/stats.py`, `cogs/verify.py` (not in `COG_EXTENSIONS`)
- Empty `cogs/clan.py/`
- Supabase write stubs in API (experimental; not hub MVP)
- Base44 Discord OAuth + two-way admin panel

### How to run

**Bot:**
```powershell
cd "C:\Users\macfl\OneDrive\Desktop\PROJECT GRAZYBOT"
.\venv\Scripts\Activate
python main.py
```

**API:**
```powershell
cd "C:\Users\macfl\OneDrive\Desktop\Grazybot-API"
pip install -r requirements.txt
$env:HUB_API_TOKEN="dev-token"
# DATABASE_URL from .env
uvicorn main:app --reload --port 8000
```

**Fallback monolith:**
```powershell
python OldPrefactor.py
```
