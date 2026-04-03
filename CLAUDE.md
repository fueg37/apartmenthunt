# CLAUDE.md — Apartment Hunt

Personal tool for tracking and scraping apartment listings, gyms, and hospitals in the Palm Beach, FL area. Combines a CLI (Typer) with a FastAPI web dashboard and SQLite storage.

## Project Layout

```
main.py               # CLI entry point (Typer app)
models.py             # Pydantic data models (Location hierarchy)
config.py             # Settings & constants (Pydantic Settings, .env)
seed_data.py          # Known locations to pre-populate the DB

commands/             # One file per CLI command (add, show, search, scrape, diff)
scrapers/             # HTTP-based scrapers (RentCast API, Google Places API)
db/                   # SQLite layer: connection, schema, per-table CRUD modules
web/                  # FastAPI server + Jinja2 template + Leaflet map frontend
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # add GOOGLE_PLACES_API_KEY and RENTCAST_API_KEY
python main.py seed              # populate DB with known locations
```

macOS shortcut: `./hunt.command` — creates the venv and runs the app automatically.
Windows shortcut: `hunt.bat`

## Common Commands

```bash
python main.py seed                                          # seed DB
python main.py show                                          # list all locations
python main.py show --type apartment --max-price 3500 --available --top-picks
python main.py add                                           # add a location interactively
python main.py add-file locations.csv                        # bulk import
python main.py scrape                                        # scrape live unit data
python main.py search --type apartment --area boynton --max-price 3500
python main.py search --type gym --query "powerlifting" --min-rating 4.0
python main.py diff                                          # show changes since last scrape
python main.py web                                           # start dashboard at http://localhost:8000
```

## Architecture Notes

- **Async-first**: all DB and HTTP I/O is async (`aiosqlite`, `httpx`). CLI commands bridge sync→async with `asyncio.run()`.
- **Polymorphic models**: `Location` is the base; `Apartment`, `Gym`, `Hospital`, `PointOfInterest` are subtypes. Type-specific fields are stored as JSON in `extra_json`; `location_from_row()` hydrates the right subclass.
- **Scrapers use ABC**: `BaseScraper` (`scrapers/base.py`) is a minimal ABC for stateless HTTP scrapers; subclasses implement `scrape()`. `rentcast.py` queries the RentCast API via `httpx`; `google_places.py` queries the Google Places API.
- **Database**: SQLite with WAL mode. Schema is in `db/schema.sql`. Tables: `locations`, `units`, `scrape_events`, `change_log`, `discoveries`.
- **Web dashboard**: single FastAPI server (`web/server.py`) serves a Jinja2 template with an embedded Leaflet map. JSON API endpoints feed JS on the page.
- **Config**: `config.py` uses Pydantic Settings; values come from `.env`. Key constants: `SEARCH_BBOX` (Boca Raton→Lake Worth Beach), `AREA_CENTERS`, scrape delay range.

## Environment Variables (`.env`)

```
GOOGLE_PLACES_API_KEY=...
RENTCAST_API_KEY=...
DB_PATH=data/hunt.db                  # default; directory is git-ignored
SCRAPE_DELAY_MIN=1
SCRAPE_DELAY_MAX=3
SCRAPE_APARTMENT_TTL_HOURS=24         # skip re-scraping apartments updated within this window
```

## No Test Suite

There are no automated tests. Manually verify with `python main.py show` and `python main.py web`.

## Key Dependencies

| Package | Purpose |
|---|---|
| `typer` | CLI framework |
| `fastapi` / `uvicorn` | Web dashboard |
| `pydantic` / `pydantic-settings` | Models & config |
| `aiosqlite` | Async SQLite |
| `httpx` | Async HTTP (RentCast / Google Places API calls) |
| `tenacity` | Retry logic |
| `rich` | CLI formatting |
| `apscheduler` | Background scheduling |
