<?php

require __DIR__ . '/calllog_queue_lib.php';

try {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
        calllog_json_response(405, ['ok' => false, 'error' => 'POST required']);
    }

    $config = calllog_load_config();
    $manifestJson = (string) ($_POST['manifest'] ?? '');
    $signature = (string) ($_POST['signature'] ?? '');

    if ($manifestJson === '' || $signature === '') {
        throw new RuntimeException('Missing manifest or signature');
    }

    $manifest = calllog_validate_manifest($config, $manifestJson, $signature);
    $stored = calllog_store_upload_batch($config, $manifest, $_FILES);

    $result = [
        'ok' => true,
        'batch_id' => $stored['batch_id'],
        'stored' => true,
    ];

    if (!empty($config['auto_process_after_upload'])) {
        $result['process'] = calllog_process_queue($config);
    }

    calllog_json_response(200, $result);
} catch (Throwable $e) {
    if (isset($config) && is_array($config)) {
        calllog_log($config, 'Upload error: ' . $e->getMessage());
    }
    calllog_json_response(400, ['ok' => false, 'error' => $e->getMessage()]);
}
