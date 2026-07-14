<?php

return [
    'owner' => 'libertyrights',
    'repo' => 'sbco-calllog-scraper',
    'workflow' => 'sbco-calllog.yml',
    'ref' => 'main',
    'github_token' => 'github_pat_or_classic_token_here',
    'trigger_token' => 'separate_random_secret_for_optional_http_trigger',
    'dispatch_if_upload_older_than_seconds' => 18 * 60,
    'min_seconds_between_dispatches' => 15 * 60,
];

