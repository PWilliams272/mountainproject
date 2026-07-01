# mountainproject

Python package for scraping Mountain Project area trees into structured files.

It is designed around the way the site actually exposes data today:

- Area pages and route pages are mostly server-rendered HTML.
- Comments are loaded from HTML fragments under `/comments/forObject/...`.
- Route stats are exposed via `/api/v2/routes/<route_id>/{stars,ratings,todos,ticks}`.
- Photo pages expose direct image assets via page markup such as `og:image`.

The package exports:

- one JSON file per area
- one JSON file per route
- consolidated `areas.jsonl` and `routes.jsonl`
- flattened `comments.jsonl` and `photos.jsonl`
- optional route stats tables in `route_stats_summary.jsonl`, `route_stars.jsonl`, `route_suggested_ratings.jsonl`, `route_todos.jsonl`, and `route_ticks.jsonl`
- local Parquet snapshots and a DuckDB database under `structured/`
- optional raw HTML snapshots
- optional downloaded image files

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

The CLI now shows progress bars and an elapsed timer by default. Use `--no-progress` if you want plain log output instead.

By default, exports now live under `data/exports/<area_slug>`, and sibling exports in that catalog are reusable inputs for future pulls.

## Package layout

The codebase now has a split between scraping, storage, and domain models:

- `mountainproject.scraper`: scraping CLI and crawl/fetch logic
- `mountainproject.storage`: export catalog and structured storage
- `mountainproject.reader`: export loading and query helpers
- `mountainproject.domain`: shared record models

Scraper code is now canonical under `mountainproject.scraper`. Shared record models are canonical under `mountainproject.domain.models`.

## Usage

List state roots that have already been pulled:

```bash
mountainproject list-pulled-states
```

List state roots that have not been pulled yet:

```bash
mountainproject list-unpulled-states
```

Preview the remaining state queue without scraping:

```bash
mountainproject pull-unpulled-states --dry-run
```

Pull a state by name instead of passing the full Mountain Project area URL:

```bash
mountainproject pull-state colorado
```

By default, `pull-state` skips states that already have a completed full-depth manifest. Use `--no-skip-if-pulled` if you want to force a rerun.

List the continent roots under International:

```bash
mountainproject list-continent-area-urls
```

Preview all remaining continent pulls under International without scraping:

```bash
mountainproject pull-international --dry-run
```

Pull a single continent by name:

```bash
mountainproject pull-continent europe
```

`pull-international` behaves like the state queue command for continents: by default it skips any continent that already has a completed full-depth manifest, and `--no-skip-if-pulled` forces a rerun of every continent.

By default, `scrape-area` and the state-based commands now use full-depth crawling, fetch route stats, and resume the output directory instead of truncating it.

Scrape a single area page and all routes directly listed on it:

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/116147434/main-wall" \
  --output-dir data/exports/main_wall \
  --max-depth 0 \
  --delay-seconds 0.1 \
  --progress \
  --http-cache-mode ephemeral \
  --fetch-comments \
  --resolve-photo-pages
```

Include route-level stars, suggested ratings, to-do users, and ticks:

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/116147434/main-wall" \
  --output-dir data/exports/main_wall \
  --max-depth 0 \
  --delay-seconds 1.0 \
  --progress \
  --route-workers 8 \
  --fetch-route-stats \
  --route-stats-workers 8
```

Hydrate only the missing route stats for an existing export without re-scraping route pages:

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/105931166/central-pinnacles" \
  --output-dir data/exports/central_pinnacles \
  --fetch-route-stats \
  --hydrate-missing-route-stats-only \
  --progress \
  --http-cache-mode ephemeral \
  --route-stats-workers 8
```

Recurse into child areas:

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/105708963/south-dakota" \
  --output-dir data/exports/south_dakota \
  --max-depth 2 \
  --delay-seconds 0.1
```

Or crawl the full in-scope subtree until no more descendant areas remain:

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/105805238/holcomb-valley-pinnacles" \
  --output-dir data/exports/holcomb_valley_pinnacles \
  --full-depth \
  --delay-seconds 0.1
```

Pass authenticated session cookies when you need deeper comment access:

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/116147434/main-wall" \
  --output-dir data/exports/main_wall \
  --cookie session_name=session_value \
  --cookie other_cookie=other_value
```

Log in with Mountain Project credentials when you want the scraper to request comment threads with an authenticated session:

```bash
export MOUNTAINPROJECT_EMAIL="you@example.com"
export MOUNTAINPROJECT_PASSWORD="your-password"

mountainproject scrape-area \
  "https://www.mountainproject.com/area/116147434/main-wall" \
  --output-dir data/exports/main_wall \
  --fetch-comments \
  --progress
```

Or point the scraper at a local JSON credentials file:

```json
{
  "email": "you@example.com",
  "password": "your-password"
}
```

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/116147434/main-wall" \
  --output-dir data/exports/main_wall \
  --fetch-comments \
  --auth-credentials-file /absolute/path/to/mountainproject-auth.json
```

If `./mountainproject-auth.json` exists in the project root, the scraper now uses it automatically when you do not pass explicit credentials or cookies.

Download resolved image files:

```bash
mountainproject scrape-area \
  "https://www.mountainproject.com/area/116147434/main-wall" \
  --output-dir data/exports/main-wall \
  --resolve-photo-pages \
  --download-images
```

If you omit `--output-dir`, the scraper derives one automatically as `data/exports/<area_slug>`.

When `--reuse-catalog` is on, the scraper will reuse matching areas, routes, and route stats from sibling export directories under `data/exports/`. That is now the default behavior, so overlapping subtree pulls can skip previously saved records while still writing a self-contained export directory for the new run.

## Programmatic access

Use the loader and query helpers when you want to work with previously scraped exports in notebooks, analysis code, or an application layer:

```python
from mountainproject.reader.loaders import load_exports
from mountainproject.reader.queries import dataset_counts, route_comment_counts

loaded = load_exports(prefer_names=["holcomb_valley_pinnacles"])
try:
    print(dataset_counts(loaded))
    print(route_comment_counts(loaded, output_name="holcomb_valley_pinnacles").head())
finally:
    loaded.close()
```

`load_exports(...)` reads the JSONL tables for one or more exports and opens the export DuckDB file automatically when exactly one export is selected.

## Output layout

```text
data/
  exports/
    holcomb_valley_pinnacles/
      manifest.json
      areas.jsonl
      routes.jsonl
      comments.jsonl
      photos.jsonl
      skipped_requests.jsonl
      route_stats_summary.jsonl
      route_stars.jsonl
      route_suggested_ratings.jsonl
      route_todos.jsonl
      route_ticks.jsonl
      areas/
        116147434.json
      routes/
        105714713.json
      route_stats/
        105714713.json
      structured/
        mountainproject.duckdb
        parquet/
          areas.parquet
          routes.parquet
          comments.parquet
          photos.parquet
          route_stats_summary.parquet
      raw/
        areas/
        routes/
        comments/
        photos/
      images/
```

## Notes

- Mountain Project's `robots.txt` currently publishes `Crawl-delay: 60` and disallows `/ajax*` and `/data*`.
- The scraper now defaults to an aggressive 0.1 second global delay between uncached requests, shared across all worker threads.
- Full HTTP response-body caching now defaults to `ephemeral`, so cached HTML and JSON responses speed up a run without becoming long-lived storage.
- `ephemeral` means the HTTP response cache lives only in memory for the lifetime of the current Python process. It is shared across worker threads during that run, but it disappears if the process exits or you press `Ctrl+C`.
- Use `--http-cache-mode persistent` only when you explicitly want durable response caches on disk. Use `disabled` to bypass response caching entirely.
- Comments can be partial when unauthenticated. The exported record marks `comments_truncated` when the site exposes a `Show N More Comments` control instead of returning the full thread.
- Authenticated login is now supported with `MOUNTAINPROJECT_EMAIL` and `MOUNTAINPROJECT_PASSWORD`, or `--auth-credentials-file`. Since authenticated pulls are the durable path forward, keep canonical exports under `data/exports/` and prefer login-backed runs when building that catalog.
- If no explicit auth flags or env vars are provided, the CLI will automatically use `./mountainproject-auth.json` when that file exists.
- `--max-depth` is a manual area-to-area hop limit from the starting area. Use `--full-depth` if you want the crawler to stop only when it runs out of in-scope descendant areas.
- Route stats are fetched separately and are now on by default because they are part of the normal full export flow for this project.
- Route pages can be scraped with bounded threading via `--route-workers`, and route stats fetching supports bounded threading via `--route-stats-workers`. Both default to 8 workers.
- All uncached requests, including threaded route and route-stats fetches, now share one global rate limiter controlled by `--delay-seconds`.
- If any request receives HTTP 429, the scraper now pauses all threads for 90 seconds from the latest 429 response before issuing the next request. A second 429 before any successful response completes aborts the run so you can resume later with a higher `--delay-seconds`.
- Transient transport failures such as connection drops and request timeouts are retried automatically with exponential backoff. Transient server-side failures such as HTTP `500`, `502`, `503`, and `504` are also retried. If the network stays down or the server keeps failing, rerun the same command with `--resume-output` after connectivity returns.
- Route pages or route-stats endpoints that still fail after retries, whether from persistent `5xx` responses or transport-level failures such as DNS/connection errors, are skipped and recorded in `skipped_requests.jsonl` inside the export directory so they can be reviewed or backfilled later.
- `--reuse-catalog` now reuses saved areas, routes, and route stats from sibling export directories under the catalog root, so overlapping pulls can skip previously saved records without sharing one giant output directory.
- `--resume-output` is now the default. Use `--fresh-output` if you explicitly want to truncate and rebuild an existing output directory.
- After killing a run, continue it by rerunning the same command with `--resume-output` against the same output directory. Resume uses the saved JSON and JSONL exports on disk; it does not need the in-memory `ephemeral` cache to survive.
- Local structured storage is materialized automatically into Parquet and DuckDB from the JSONL tables. Treat those structured tables as the durable local development store.
- Route and area records include photo-page links even when direct image URLs are not resolved.
- Re-running against the same output directory can still reuse a persistent HTTP cache under `.cache/http` when `--http-cache-mode persistent` is enabled.
