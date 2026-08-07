# 🚗 Suzuki Ignis Sport Europe Hunter

A continuously-running discovery system that scans the publicly reachable
European used-car web for a rare **Suzuki Ignis Sport (1st gen, chassis HT81S)**
— with a deliberate bias toward the **long tail**: small dealers, local garages,
Suzuki dealers, youngtimer/JDM specialists, national classifieds, forums and
clubs — the sources other Ignis Sport hunters *don't* watch.

> Goal: **find the car before everyone else.** Aggregators like AutoScout24,
> mobile.de, TheParking and AutoUncle are only the baseline. The real value is
> in discovering obscure sources and unmasking cars that were **mislabelled** as
> a plain "Suzuki Ignis 1.5".

This is not a one-off search and not a demo. It is designed to run for months on
a small VPS and get better over time at surfacing listings you'd otherwise miss.

---

## What it does

- **Deterministic core, AI where it earns its keep.** Fast, cheap, testable code
  handles HTTP, parsing (JSON-LD / RSS / XML / HTML), price/mileage/power
  extraction, dedup, scheduling and scoring. Claude is used *only* for the
  genuinely ambiguous decisions (see [AI layer](#ai-layer)).
- **Mislabelled-Sport hunting.** A bare `Suzuki Ignis 1.5 2004` with `80 kW` and
  "Sportsitze/Spoiler" is flagged and, if still uncertain, escalated to the AI
  Vehicle Detective. Finding these is the single most important job.
- **Europe-wide, multilingual.** 28 countries, queries generated in each local
  language plus English and the language-independent `HT81S` chassis code.
- **Source discovery engine.** Continuously finds and classifies *new* domains;
  it is not a static portal list.
- **Full history.** Price drops, status changes, disappearance/relisting — never
  lost. A single failed request never marks a car SOLD.
- **Cross-platform dedup.** The same car on a dealer site + AutoScout + a
  regional portal collapses into one listing that keeps every URL (preferring
  the dealer's own page).
- **Explainable scoring.** Every hit gets a 0–100 Vehicle Match Confidence and,
  when active, an Opportunity Score with a "why 87/100?" breakdown.
- **Web dashboard, notifications, scheduler, backups** — all included.

---

## Quick start

```bash
# 1. Install dependencies (Python 3.10+)
python -m pip install -r requirements.txt

# 2. Configure (optional — everything works without any secrets)
cp .env.example .env      # then edit if you have API keys / notification tokens

# 3. Initialise the database and seed the European source inventory
python -m src.cli init

# 4. Run a scan cycle (respects robots.txt & rate limits)
python -m src.cli scan --limit 25 --force

# 5. Run source discovery (needs a search provider key for external search;
#    otherwise it relies on seeds + sitemaps)
python -m src.cli discover

# 6. Start the dashboard
python -m src.cli dashboard        # http://localhost:8000

# 7. Print the initial discovery report
python -m src.cli report
```

Run the tests:

```bash
python -m pytest -q
```

---

## Environment variables

Everything is read from the environment (a `.env` file is auto-loaded).
**No secret is ever hardcoded, logged, or stored in the database.** See
[`.env.example`](.env.example) for the full annotated list. Highlights:

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Enables the Claude AI layer. **Absent → deterministic fallback.** | _(unset)_ |
| `ANTHROPIC_MODEL` / `ANTHROPIC_MODEL_FAST` | Model names (configurable, not hardcoded) | `claude-opus-4-8` / a fast model |
| `AI_MONTHLY_BUDGET_USD` | Hard monthly spend guard for AI | `25` |
| `DATABASE_URL` | SQLite by default; swap to `postgresql+psycopg://…` later | `sqlite:///data/ignis_hunter.db` |
| `SCAN_TIMES` / `TIMEZONE` | Scan schedule (≥4/day) and timezone | `00:00,06:00,12:00,18:00` / `Europe/Berlin` |
| `SEARCH_PROVIDER` | `none` / `serpapi` / `brave` / `google_cse` | `none` |
| `NOTIFY_CHANNELS` | `console,database,telegram,discord,email` | `console,database` |
| `MATCH_CONFIDENCE_THRESHOLD` / `HOT_ALERT_CONFIDENCE` | Display + alert thresholds | `60` / `85` |

> **No credential is required to run.** Missing optional keys never stop the
> system — it falls back to deterministic heuristics, console/DB notifications,
> and seed+sitemap discovery.

---

## Database setup

- SQLite by default (WAL mode, foreign keys on). `python -m src.cli init`
  creates the schema and seeds ~65 European sources.
- **Migrations:** the schema is created with `Base.metadata.create_all`; the
  design is PostgreSQL-portable. To move to Postgres, set `DATABASE_URL` and run
  `init` against the new database.
- **Backups:** automatic timestamped copies in `data/backups/` (nightly + after
  each scan), keeping the last 14. Run manually with `python -m src.cli backup`.
  For Postgres use `pg_dump`.

---

## AI layer

Claude is invoked **only when semantic judgement adds real value**, never for
work deterministic code does better. Five modules, each with a Pydantic-validated
structured output and a deterministic fallback:

1. **Vehicle Detective** — is this *ambiguous* candidate really an Ignis Sport?
   (Only the pre-filter's `NEEDS_AI` bucket reaches it.)
2. **Source Hunter** — is a newly discovered domain a place a Sport could be sold,
   and how do we crawl it?
3. **Listing Analyst** — condition/risk extraction + translation for serious hits
   (only when the description hash changed → no re-billing).
4. **Image Vehicle Detective** — visual Sport cues (spoiler, skirts, sport
   wheels/seats…) as a *supporting indicator only*.
5. **Parser Diagnostic** — when a healthy source suddenly returns nothing,
   suggests a cause + fix. **Claude never edits production code.**

**Cost control:** every call is logged to `ai_usage` with token counts and an
estimated cost; a monthly budget guard and on-disk response cache prevent
runaway spend. The funnel keeps AI calls tiny — most candidates are decided
deterministically.

To enable: set `ANTHROPIC_API_KEY` (and optionally the model names). That's it.

---

## Adding a source / analysing a URL

```bash
# Add & immediately scan a dealer/source you found
python -m src.cli add-source https://autohaus-example.de --country DE

# Analyse a single listing URL: is it a Sport? already known? who's the seller?
python -m src.cli analyze-url "https://…/suzuki-ignis-…"
```

Both are also available from the **Sources** page in the dashboard.

---

## Notifications

Console + database always work with zero configuration. Enable more channels via
`NOTIFY_CHANNELS` and the matching env vars:

- **Telegram** — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Discord** — `DISCORD_WEBHOOK_URL`
- **Email (SMTP)** — `SMTP_HOST`, `SMTP_FROM`, `SMTP_TO`, …

A strong new hit (`confidence ≥ HOT_ALERT_CONFIDENCE`, good price, low km, fresh,
long-tail source, LHD) fires a **HOT** alert; other hits are stored with normal
priority.

---

## Scheduler

`python -m src.cli scheduler` runs an APScheduler process (no terminal/browser
required) that performs:

- a **full scan** at each `SCAN_TIMES` entry (≥4/day),
- **source discovery** once daily at `DISCOVERY_HOUR`,
- a **high-priority sweep** every 2h (priority ≥ 70 sources),
- a **nightly DB backup**.

Failed sources are retried with backoff; conditional requests (ETag /
Last-Modified) and sitemap/feed state avoid re-crawling everything each time.

---

## Debugging a crawler

- `python -m src.cli scan --limit 5 --force` and watch `logs/ignis_hunter.log`.
- A source that returns 0 where it used to return many is flagged
  `POSSIBLE_PARSER_FAILURE` and shown on the **Health** page (with a diagnosis).
- `403/429`, captchas, logins and paywalls are **respected, never bypassed** — a
  source that can't be crawled politely is stored as `MANUAL_REVIEW_SOURCE`.

---

## Real-world validation (Phase 17)

The system is built to be proven on a real machine with unrestricted outbound
internet. Key commands:

```bash
# Prove connectivity/APIs/sources actually work on this host:
python -m src.cli network-test

# Multi-provider discovery (Brave AND optionally SerpApi, budget-guarded):
python -m src.cli discover --max-queries 60

# Give every source a GROUND-TRUTH status from a real fetch:
python -m src.cli validate-sources

# Per-country coverage gap audit (where to expand next):
python -m src.cli audit-sources

# The Real-World Validation Report (scan + discovery + vehicles + long-tail
# wins + provider comparison + source failures):
python -m src.cli real-report
```

**Search providers** are multi-provider: Brave is the primary raw-web-results
provider, SerpApi is an optional complementary Google index. Enable both with
`SEARCH_PROVIDER_BRAVE_ENABLED=true` / `SEARCH_PROVIDER_SERPAPI_ENABLED=true`.
Every discovered domain records **which provider(s)** surfaced it and at what
**rank/page**, so `real-report` shows Brave-unique vs SerpApi-unique vs overlap.

**Budgets** keep spend bounded and separate monitoring from discovery: listing
monitoring of known sources runs ≥4×/day and uses **no** search API; source
discovery runs ~1×/day within `BRAVE_MONTHLY_REQUEST_BUDGET` /
`SERPAPI_MONTHLY_REQUEST_BUDGET` / `ANTHROPIC_MONTHLY_COST_BUDGET` (warn at 80%,
stop non-critical calls at 100%). Query **rotation** favours high-yield and
under-explored queries instead of re-running hundreds of near-identical ones.

**Seller → Source expansion:** whenever a real listing is ingested — even from
AutoScout/mobile/TheParking — the system tries to identify the seller's own
dealer domain, registers it as a monitorable source, and stores the full
provenance chain (`discovery_source → aggregator → original_marketplace →
seller → dealer_domain → canonical_listing`).

---

## Deployment on a fresh Ubuntu/Debian VPS

**1 — Exact deployment commands (fresh Ubuntu):**

```bash
git clone https://github.com/justtheboisgang/suzuki.ignis.sport.scanner
cd suzuki.ignis.sport.scanner
./scripts/setup_server.sh          # installs Docker, prepares .env, builds
nano .env                          # add your keys (see below)
```

**2 — Environment variables you must set yourself** (in `.env`):
- `ANTHROPIC_API_KEY` — enables the Claude intelligence layer.
- `BRAVE_SEARCH_API_KEY` + `SEARCH_PROVIDER_BRAVE_ENABLED=true` — real discovery.
- *(optional)* `SERPAPI_API_KEY` + `SEARCH_PROVIDER_SERPAPI_ENABLED=true`.
- *(optional)* `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` for phone alerts.
- Budgets/timezone as desired (sensible defaults ship in `.env.production.example`).

**3 — External accounts / API keys to create:**
- **Anthropic** — https://console.anthropic.com (for `ANTHROPIC_API_KEY`).
- **Brave Search API** — https://brave.com/search/api/ (for `BRAVE_SEARCH_API_KEY`).
- **SerpApi** *(optional)* — https://serpapi.com.
- **Telegram bot** *(optional)* — via @BotFather.

**4 — One command for network validation:**
```bash
sudo docker compose run --rm scheduler python -m src.cli network-test
```

**5 — One command for the first real full scan:**
```bash
sudo docker compose run --rm scheduler python -m src.cli scan --force
```
(Then discovery + report:
`... python -m src.cli discover --max-queries 60` and `... real-report`.)

**6 — One command to start the permanent service:**
```bash
sudo docker compose up -d
```
Both containers have `restart: unless-stopped` and healthchecks, so they come
back after crashes and reboots.

**7 — Dashboard URL/port:** `http://<SERVER_IP>:8000`
(overview, listings, sources, health & coverage; liveness at `/healthz`).

**8 — Verify the four daily scans are actually running:**
```bash
sudo docker compose logs scheduler | grep "full scan"     # job start lines
sudo docker compose run --rm scheduler python -m src.cli stats
# 'last_scan' timestamp + scan_runs advance after each 00/06/12/18 slot.
```
Scans are logged in the `scan_runs` table (one row per cycle) and on the
dashboard's **Overview** ("Last scan …").

**9 — Where to see logs and errors:**
- `sudo docker compose logs -f scheduler` (live) and `logs -f dashboard`.
- Persistent file log in the `ignis-logs` volume (`logs/ignis_hunter.log`).
- Per-scan errors in `scan_runs.errors`; source problems on the **Health** page.

**10 — Current real-world-validation status:** run
`python -m src.cli real-report` — it prints exactly what was really fetched,
discovered and classified (and says plainly if no Sport was found yet, without
inventing one).

**systemd alternative (no Docker):** install into `/opt/ignis-hunter`, create a
venv (`python -m venv .venv && .venv/bin/pip install -r requirements.txt`), then:

```bash
sudo cp deploy/ignis-hunter-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ignis-hunter-scheduler ignis-hunter-dashboard
```

Both survive reboots and need no attached session. Playwright is optional and
only needed for the few JS-only sources (flagged `requires_browser`); the core
runs without a browser.

---

## Security

- Secrets **only** via environment variables; `.env` is git-ignored.
- API keys are never logged, never stored in the database, never printed in
  errors or screenshots.
- The crawler identifies itself, throttles per host, and obeys `robots.txt`.

---

## A note on honesty

The system **never** claims to have "searched the whole internet" — that's
impossible. It continuously **maximises coverage** of the publicly findable
European market and shows exactly which countries/sources are covered, which
work, which fail, and when each was last checked. Unknown values stay
`NULL/UNKNOWN`; nothing is guessed and then stored as fact. Every field traces
back to a source and is tagged `FACT` / `INFERRED` / `AI_INFERENCE` / `UNKNOWN`.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.
