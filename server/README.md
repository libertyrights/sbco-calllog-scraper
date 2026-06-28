# Call Log Queue Receiver

This folder contains the server-side queue receiver for the call log uploader.

Files:
- `upload_calllog_batch.php`
  Receives signed multipart uploads, stores them in a queue batch directory, and optionally processes them immediately.
- `process_calllog_queue.php`
  Processes queued batches in timestamp order, promotes live files, triggers the downstream rebuild, and deletes processed batch files.
- `calllog_queue_lib.php`
  Shared queue and promotion logic.
- `calllog_server_config.example.php`
  Example server configuration template.

## Security Model

- Signed manifest using `upload_secret`
- Upload timestamp age limit
- Optional UTC upload window whitelist for expected hourly runs
- Optional separate `process_token` for manual/cron processor invocation

The upload window can be configured either as:
- Explicit `allowed_hours` and `allowed_minutes` lists
- A fallback `hour_modulo` plus `minute_start` and `minute_end` range

The receiver enforces that whitelist against the server's current UTC time when the batch arrives. For the GitHub Actions hourly schedule, the recommended setup is explicit hour and minute whitelists so the receiver only accepts batches that arrive inside the expected cadence.

## Queue Behavior

- Upload batches are written into `incoming/<batch-id>/`
- Live files are promoted in timestamp order
- Successfully processed batch directories are deleted
