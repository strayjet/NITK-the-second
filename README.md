# CityMind — Merged Project (final)

This is the merged result of two uploaded CityMind zips. Full architecture
docs are preserved in `ARCHITECTURE.md` (the original project README) —
this file covers what changed in the merge and how to run it.

## What was actually different between the two zips

Before merging, both zips were diffed and inspected in full. Worth knowing:

- **The frontend was byte-for-byte identical in both zips.** There were no
  "blank" pages to fix — every route (Overview, Incidents, Map, Planning,
  Copilot, Settings) was already fully implemented, with loading/error/empty
  states (`ui.js`) already wired to a real API client (`api.js`) for every
  request.
- **The road-closure simulation logic (`app/core/city_logic.py`) differed by
  one line** — a fallback file-path constant that's overridden by config
  either way. Both zips run the same routing/closure engine
  (`/city/simulate-closure`, `/city/block-road`, `/city/unblock-road`, live
  dynamic rerouting).
- The real difference was **infra robustness**: the "repaired" zip's
  `entrypoint.sh` auto-downloads the WorldPop raster and YOLO checkpoint at
  startup and uses a Docker named volume instead of a bind-mount that
  silently created an empty directory; the other zip required manually
  placing those files. The "repaired" zip's version was used as the base for
  this reason.

## What was changed for this merge

1. **AI backend swapped from Anthropic (Claude) to Gemini.**
   The existing "Copilot" feature (`/copilot/chat`) previously used the
   Anthropic Messages API for LLM-backed tool-calling (with a fully
   functional deterministic rule-based fallback when no key was set — there
   was no OpenAI/Copilot-the-product dependency anywhere in the codebase).
   It now calls the Gemini API (`gemini-2.0-flash`, `generateContent`) with
   the same tool schemas translated to Gemini's `function_declarations`
   format. Set `GEMINI_API_KEY` to enable it; without a key (or if the
   Gemini call fails for any reason), `/copilot/chat` transparently falls
   back to the rule-based classifier, exactly as before.

2. **New endpoint: `POST /ai/query`.**
   A separate, simpler free-text Gemini endpoint (`app/api/ai_routes.py`,
   `app/services/gemini_service.py`) for general questions that don't map to
   a specific CityMind tool. Body: `{"prompt": "...", "system": "optional"}`.
   Always returns HTTP 200 — if `GEMINI_API_KEY` is unset or the request
   fails, `source` is `"fallback"` and `answer` explains why, instead of a
   500 or an empty body.

3. **`npm run dev` support added to the frontend.**
   The frontend is (by design) a no-build vanilla JS app — native ES modules
   served directly, no bundler, no framework. There was previously no
   `package.json` at all. A minimal one was added using **Vite purely as a
   zero-config local dev server**; production deployment is unchanged (still
   plain static files behind nginx — see `frontend/Dockerfile`).

## Known limitations (being upfront about them)

This sandbox had no network access while assembling this, so:
- `pip install -r backend/requirements.txt` and `npm install` were **not**
  run here — you'll need to run them yourself.
- The Gemini API calls could not be tested against a live API key.
- What **was** verified: every backend `.py` file compiles cleanly
  (`py_compile`, including every new/edited file), and every frontend `.js`
  file passes `node --check`. A full end-to-end run has not been executed.

Also, two things depend on external assets not included (unrelated to this
merge — same in both original zips):
- `CITY_LOGIC_ENABLED=true` (default) needs internet access to Overpass at
  first startup to build the road graph, and a WorldPop raster (auto-fetched
  by `entrypoint.sh`/`fetch_worldpop.py` if running via Docker; **not**
  auto-fetched if you run `uvicorn` directly, see below). If unavailable,
  `/city/*` returns a clean `503`, not a crash.
- `accident_best.pt` (fine-tuned accident-detection checkpoint) has no public
  source and isn't included — the ML service still starts fine without it;
  accident detection just reports `accident_model_loaded=false`.

## Run steps

### Backend

```bash
cd citymind/backend
python -m venv .venv && source .venv/bin/activate   # or your preferred env tool
pip install -r requirements.txt
cp .env.example .env                                  # edit as needed, e.g. GEMINI_API_KEY
# Postgres must be reachable at DATABASE_URL (see .env.example) for /dashboard/*
# and persistence to work; the app itself still starts without it.
alembic upgrade head
uvicorn app.main:app --reload --port 9000
```

The `--port 9000` matters: the frontend's default API base (`js/state.js`)
and `docker-compose.yml` both assume the backend is on port `9000`. Plain
`uvicorn app.main:app --reload` (no `--port`) defaults to `8000`, which will
not match the frontend unless you also change the API base in the Settings
page or `js/state.js`.

Without `CITY_LOGIC_ENABLED=false` and without a raster at
`CITY_LOGIC_WORLDPOP_TIF_PATH`, `/city/*` will return `503` when run this way
(no `entrypoint.sh` auto-fetch step outside Docker). Run
`python scripts/fetch_worldpop.py` first if you want `/city/*` live without
Docker, or use `docker compose up` instead, which runs the full auto-fetch
pipeline.

### Frontend

```bash
cd citymind/frontend
npm install
npm run dev
```

Open the printed local URL (default `http://localhost:5173`). No build step
is needed for production — `frontend/Dockerfile` serves the raw files via
nginx as-is.

### Everything via Docker (closest to "just works")

```bash
cd citymind
cp backend/.env.example backend/.env   # add GEMINI_API_KEY if you want live AI
docker compose up --build
```

This runs Postgres, Redis, the ML service, the backend (with auto-fetch of
the WorldPop raster and YOLO weights), and the frontend together, with ports
matching the defaults above.

## Environment variables to know about

| Variable | Where | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | backend `.env` | Enables live Gemini-backed `/copilot/chat` and `/ai/query`. Optional — both work without it. |
| `CITY_LOGIC_ENABLED` | backend `.env` | Set `false` to skip the routing/simulation engine entirely (e.g. offline/CI). |
| `DATABASE_URL` | backend `.env` | Postgres connection string; needed for `/dashboard/*` and persistence. |
| `YOLO_SERVICE_BASE_URL` | backend `.env` | Where the ML service is reachable from the backend. |

See `backend/.env.example` for the full list with defaults and explanations.
