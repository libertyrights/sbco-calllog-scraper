# SBCO Call Log Scraper

This repository contains the public-safe scraper and uploader pieces for the San Bernardino County Sheriff call log automation.

Included:
- `scraper_run.py`
  Pulls the call log, writes local outputs, and uploads signed batches to the server-side queue receiver.
- `arrest_index_builder.py`
  Builds `calllog_arrest_index.json`, including arrest matches, death-index matches, and coroner/base-call associations.
- `scrape-sbco-arr-log.py`
  Arrest log scraper source, included as a companion script.
- `server/`
  PHP queue receiver, processor, and example config for the serv00 side.
- `site/trigger_github.php`
  Optional serv00-side cron trigger that dispatches the GitHub Actions scraper when the live upload is stale.
- `local_support_refresh.py`
  Local-only arrest/release/death support refresher. It does not scrape SBSO/CHP/Fire calls and does not publish the live call log.
- `ops/local/`
  Windows PowerShell wrappers for separated local lanes: SBSO/CHP/Fire feed scraping versus arrest/release/death support refresh.
- `.github/workflows/sbco-calllog.yml`
  GitHub Actions workflow that runs every 20 minutes.

Not included:
- Private credentials
- Local runtime state
- Internal notes
- Broader project files that are not needed for the public automation repo

## Required GitHub Secrets

Add these repository secrets before enabling scheduled runs:

- `SBCO_SERVER_CALLLOG_URL`
- `SBCO_UPLOAD_SIGNING_PRIVATE_KEY`
- `SBCO_REMOTE_DB_REBUILD_TOKEN`
- `SERV00_FTP_HOST`
- `SERV00_FTP_USER`
- `SERV00_FTP_PASS`

## Notes

- The GitHub job is scheduled for every 20 minutes.
- `site/trigger_github.php` is safe to deploy publicly, but its GitHub token must stay server-side. Use a private config outside the web root at `domains/<domain>/calllog_github_trigger_config.php`, point `SBCO_TRIGGER_CONFIG` at another config path, or provide `SBCO_GITHUB_TOKEN` in the cron command environment.
- The workflow now deploys a signed public uploader to `upnexx.xyz/osint/upload_calllog_signed.php` and signs each HTTP publish with the private key stored in `SBCO_UPLOAD_SIGNING_PRIVATE_KEY`.
- The public `sbsd.html` recovery viewer reads directly from `calllog.json`, so the live page can stay current even if the older SQLite-backed API falls behind.
- The GitHub job reuses the already-published public `all_records.json` and `death_index.csv` when those files are still fresh, and only refreshes them locally when they are stale.
- The GitHub job disables the unrelated daily release-list fetch so the hourly schedule does not create extra background traffic.
- Local feed scraping and local arrest/release/death support refresh are intentionally separate. `ops/local/run_local_calllog_scraper.ps1` defaults to SBSO/CHP/Fire feeds only, with support-file refresh and publish disabled. `ops/local/run_local_support_refresh.ps1` refreshes arrest, release, and death support data separately.
- When the release list is refreshed, the support refresher also searches Local Crime News by name and keeps the newest matching San Bernardino County Sheriff arrest before/on the release date, using age and detail-page source as matching signals. Matches are written to `release_arrest_enrichment.json`, uploaded with the other support files, and consumed by `arrest_index_builder.py`.
- If signed HTTP publish is unavailable, the GitHub job can still fall back to direct serv00 FTP publish when the serv00 secrets are present.
- The FTP fallback still needs `SBCO_REMOTE_DB_REBUILD_TOKEN` so it can call `build_calllog_db.php` after updating raw files. Without that secret, `calllog.csv` and `calllog.json` can be fresh while `sbsd_api.php` keeps serving a stale SQLite snapshot.
- Each publish now also writes a durable daily gzip snapshot named `calllog-archive-YYYYMMDD.csv.gz` plus a public `calllog_archive_index.json`, so the current term is no longer relying only on the live file and a single `.bak`.
- The server queue processor promotes files in timestamp order and deletes processed temp batches after a successful apply.
- The repo includes only example server config. Live serv00 secrets should stay in an untracked `calllog_server_config.php` on the server.

## Lightning strikes & SCE outages

The same `sbsd.html` page also hosts a Power & Weather map with two toggleable layers:

- **Lightning strikes** (default off, clickable button to avoid storm-day spam): rolling window of recent Blitzortung strikes inside the San Bernardino County bounding box.
- **SCE power outages** (default on): current San Bernardino County outages with locally mirrored details (city, affected customers, start time, estimated restore time, cause, crew status, OAN) and a link back to Edison's own outage map for that outage.

Pipeline pieces:

- `lightning_scraper.py`
  Syncs with the Blitzortung live JSON feed (`live.lightningmaps.org/l/`), drains the recent-strike backlog, keeps strikes inside the configured box and window (default 1 hour, capped at 3000), and writes `lightning_strikes.json` plus `lightning_status.json`.
- `sce_outage_scraper.py`
  Queries SCE's public ArcGIS outage service (`sce-outage-ags.esriemcs.com/.../outage/MapServer/0`), filters to the configured counties (default `SAN BERNARDINO`) and status (`ACTIVE`), and writes `sce_outages.json` plus `sce_outage_status.json`.
- `.github/workflows/lightning-outages.yml`
  Runs every 5 minutes (and on `workflow_dispatch`), executes both scrapers, and FTP-deploys the four JSON files to `/domains/upnexx.xyz/public_html/osint/`.
- `site/leaflet.js` + `site/leaflet.css`
  Vendored Leaflet 1.9.4 so the map works without a third-party CDN. Deployed to the osint directory by the main `sbco-calllog.yml` workflow.

Lightning data is provided by the Blitzortung community network; the page credits `© Blitzortung.org contributors` as requested. Non-commercial use only.

## Mojave River flood monitor

The same `sbsd.html` page also hosts a **Mojave River Flood Watch** card that tracks real-time USGS gage height on the Mojave River across all seven active gages (listed in physical order, upstream first: the West Fork above Forks Reservoir and Deep Creek, then Victorville lower narrows, Hodge, Barstow, Daggett, and Afton at the downstream end), compares each gage against the official NWS flood stages, watches the rate of rise for early warning, and publishes `mojave_river.json` plus `mojave_flood_status.json`.

- **Barstow (10262500 / MBRC1):** minor 5.0 ft / moderate 5.5 ft / major 6.0 ft
- **Victorville (10261500 / MVVC1):** minor 16 ft / moderate 18 ft / major 19 ft

Flood stages are fetched live from `water.noaa.gov` (NWS) with the values above as local fallbacks. Alerts fire on level *transitions* (or as a reminder every few hours while an elevated level persists) through optional notifiers:

- **ntfy.sh push:** set `USGS_NTFY_TOPIC`
- **SMTP email:** set `USGS_SMTP_HOST`, `USGS_SMTP_FROM`, `USGS_SMTP_TO` (plus optional `USGS_SMTP_PORT`, `USGS_SMTP_USER`, `USGS_SMTP_PASS`, `USGS_SMTP_STARTTLS`)

NWS flood watch/warning alerts for the basin (`CAZ06*` zones, configurable via `USGS_NWS_ZONE_PREFIX`) boost the card's alert level to at least WATCH and are listed in `mojave_river.json`.

Pipeline pieces:

- `usgs_flood_monitor.py`
  Fetches `00065` (gage height) and `00060` (flow) for all seven gages from the USGS NWIS Instantaneous Values API, fetches official flood stages from `water.noaa.gov`, computes a 3-hour linear rise rate, classifies each gage NORMAL/WATCH/MINOR/MODERATE/MAJOR, and fires the configured notifiers on transitions.
- `.github/workflows/usgs-flood-monitor.yml`
  Runs every 15 minutes (and on `workflow_dispatch`), executes the monitor, and FTP-deploys `mojave_river.json` + `mojave_flood_status.json` to `/domains/upnexx.xyz/public_html/osint/`. Requires only the existing `SERV00_FTP_*` secrets; the notifier secrets (`USGS_NTFY_TOPIC`, `USGS_SMTP_*`) are optional.

Useful environment knobs (optional, all with defaults):

- `USGS_FLOOD_PERIOD` (default `P3D`), `USGS_TREND_WINDOW_SECONDS` (10800), `USGS_RISE_WATCH_FT_PER_HOUR` (0.5), `USGS_RISE_WATCH_GAP_FT` (2.0), `USGS_ALERT_REMINDER_HOURS` (6), `USGS_NWS_ZONE_PREFIX` (CAZ06), `USGS_FETCH_TIMEOUT_SECONDS` (25)

Useful environment knobs (optional, all with defaults):

- `SBCO_LIGHTNING_BBOX` (default `33.5,-118.6,36.0,-114.2`), `SBCO_LIGHTNING_WINDOW_SECONDS` (3600), `SBCO_LIGHTNING_MAX_STRIKES` (3000), `SBCO_LIGHTNING_DRAIN_POLLS` (10)
- `SBCO_OUTAGE_COUNTIES` (default `SAN BERNARDINO`), `SBCO_OUTAGE_STATUS` (default `ACTIVE`), `SBCO_OUTAGE_SERVICE_URL`, `SBCO_OUTAGE_MAP_URL`
