<?php

return [
    'upload_secret' => 'replace-with-a-long-random-secret',
    'process_token' => 'replace-with-a-separate-process-token',
    'upload_max_age_seconds' => 7200,
    'accepted_upload_window_utc' => [
        'enabled' => true,
        'allowed_hours' => range(0, 23),
        'allowed_minutes' => range(10, 55),
    ],
    'queue_root' => __DIR__ . '/runtime/calllog-queue',
    'incoming_dir' => __DIR__ . '/runtime/calllog-queue/incoming',
    'live_dir' => __DIR__ . '/public/osint',
    'log_path' => __DIR__ . '/runtime/calllog-queue/calllog-queue.log',
    'allowed_remote_names' => [
        'calllog.csv',
        'calllog.json',
        'calllog_arrest_index.json',
        'death_index.csv',
        'all_records.json',
        'court_records.json',
        'record_lookup_results.json',
        'release_arrest_enrichment.json',
    ],
    'required_remote_names' => [
        'calllog.csv',
        'calllog.json',
    ],
    'auto_process_after_upload' => true,
    'rebuild_url' => 'https://upnexx.xyz/osint/build_calllog_db.php',
    'rebuild_token' => 'replace-with-build-calllog-db-token',
];
