# Architecture

## The funnel (why AI stays cheap)

```
crawl many pages
   └─► deterministic pre-filter (src/classification/prefilter.py)
         ├─ IRRELEVANT   → dropped (not even an Ignis)
         ├─ CLEAR_SPORT  → high confidence, NO AI
         ├─ CLEAR_BASE   → stored, low priority, NO AI
         └─ NEEDS_AI     → the ONLY bucket sent to Claude
                             └─► Vehicle Detective (structured verdict)
serious hit (conf ≥ 70) → Listing Analyst (condition/risk + translation)
uncertain + images      → Image Detective (visual cues, indicator only)
```

So of ~20,000 pages, only a handful of genuinely ambiguous candidates ever reach
the API. Every AI module has a deterministic fallback, so removing the API key
degrades quality, never function.

## Module map (`src/`)

| Package | Responsibility |
|---|---|
| `config/` | Settings (env only), country/language table, target-vehicle profile |
| `database/` | Engine, session, schema init, backups |
| `models/` | ORM: `sources`, `listings`, history, `discovery_queries`, `ai_usage`, `scan_runs`, feedback/notifications |
| `parsers/` | Deterministic normalisation (price/mileage/power/year/currency), JSON-LD, generic HTML |
| `classification/` | Pre-filter + Vehicle Match Confidence (0–100) |
| `crawlers/` | Polite HTTP (robots, rate limit, conditional GET), feed & sitemap crawlers |
| `ai/` | Claude client (cache, budget, usage log) + 5 structured modules |
| `discovery/` | Multilingual query generation, search providers, seed list, discovery engine |
| `deduplication/` | VIN/phone/image-hash dedup, perceptual hashing |
| `scoring/` | Explainable Opportunity Score |
| `notifications/` | console / database / telegram / discord / email + manager |
| `pipeline/` | Ingest one candidate; orchestrate scans/discovery; manual entry points |
| `scheduler/` | APScheduler jobs (≥4 scans/day, discovery, sweep, backup) |
| `dashboard/` | FastAPI + Jinja UI and read-side queries |

## Data provenance

Fields are tagged `FACT` (parsed), `INFERRED` (deterministic), `AI_INFERENCE`
(Claude) or `UNKNOWN`. Missing data is `NULL`, never guessed. LHD/RHD is inferred
from text + country with an explicit confidence, because country of origin alone
is insufficient.

## Deduplication

Identity signals: exact VIN, normalised URL, phone + price-band + mileage-band
agreement, or perceptual image similarity (pHash Hamming ≤ 8). The survivor keeps
all URLs and prefers the dealer's own page as canonical.

## Status lifecycle

`ACTIVE → MAYBE_ACTIVE → EXPIRED/REMOVED` across consecutive misses — one failed
fetch never means SOLD. Re-seen cars return to `ACTIVE` and keep their history.

## Health & self-diagnosis

Each source tracks a smoothed "typical result count". A drop to zero from a
healthy source raises `POSSIBLE_PARSER_FAILURE` and runs the Parser Diagnostic,
which *proposes* a cause/fix. Claude never edits production code; changes go
through humans/tests.

## Extending

- **New source by hand:** `add-source` (dashboard or CLI) → classify → store →
  first scan → scheduled thereafter.
- **New country:** add a row to `src/config/countries.py`; query generation and
  coverage reporting pick it up automatically.
- **Bespoke parser for a big site:** add a module under `crawlers/` and set the
  source's `parser_type`; the generic JSON-LD/HTML path is the default fallback.

## Known environmental limits

Live crawling requires outbound network access to the target sites. In sandboxes
whose network policy only allows package registries + the Anthropic API, scans
will correctly report sources as failed/blocked rather than fabricating results.
Deploy on a VPS with normal outbound access for real captures.
