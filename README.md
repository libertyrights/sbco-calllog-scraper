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
- `.github/workflows/sbco-calllog.yml`
  GitHub Actions workflow that runs hourly.

Not included:
- Private credentials
- Local runtime state
- Internal notes
- Broader project files that are not needed for the public automation repo

## Required GitHub Secrets

Add these repository secrets before enabling scheduled runs:

- `SBCO_SERVER_CALLLOG_URL`
- `SBCO_HTTP_UPLOAD_URL`
- `SBCO_HTTP_UPLOAD_SECRET`

## Notes

- The GitHub job runs hourly at minute `17`.
- The server-side receiver should use matching UTC hour and minute whitelists so it only accepts expected uploads.
- The server queue processor promotes files in timestamp order and deletes processed temp batches after a successful apply.
- The repo includes only example server config. Live serv00 secrets should stay in an untracked `calllog_server_config.php` on the server.
