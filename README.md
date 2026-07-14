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
- When the release list is refreshed locally, the support refresher also searches Local Crime News by name and keeps the newest matching San Bernardino County Sheriff arrest before/on the release date, using age and detail-page source as matching signals. Matches are written to `release_arrest_enrichment.json` and consumed by `arrest_index_builder.py`.
- If signed HTTP publish is unavailable, the GitHub job can still fall back to direct serv00 FTP publish when the serv00 secrets are present.
- The FTP fallback still needs `SBCO_REMOTE_DB_REBUILD_TOKEN` so it can call `build_calllog_db.php` after updating raw files. Without that secret, `calllog.csv` and `calllog.json` can be fresh while `sbsd_api.php` keeps serving a stale SQLite snapshot.
- Each publish now also writes a durable daily gzip snapshot named `calllog-archive-YYYYMMDD.csv.gz` plus a public `calllog_archive_index.json`, so the current term is no longer relying only on the live file and a single `.bak`.
- The server queue processor promotes files in timestamp order and deletes processed temp batches after a successful apply.
- The repo includes only example server config. Live serv00 secrets should stay in an untracked `calllog_server_config.php` on the server.
