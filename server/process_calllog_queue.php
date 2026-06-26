<?php

require __DIR__ . '/calllog_queue_lib.php';

try {
    $config = calllog_load_config();

    if (PHP_SAPI !== 'cli') {
        $providedToken = (string) ($_GET['token'] ?? $_POST['token'] ?? '');
        $expectedToken = (string) ($config['process_token'] ?? '');
        if ($expectedToken === '' || !hash_equals($expectedToken, $providedToken)) {
            calllog_json_response(403, ['ok' => false, 'error' => 'Invalid process token']);
        }
    }

    $result = calllog_process_queue($config);
    if (PHP_SAPI === 'cli') {
        echo json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL;
        exit(0);
    }
    calllog_json_response(200, $result);
} catch (Throwable $e) {
    if (isset($config) && is_array($config)) {
        calllog_log($config, 'Process error: ' . $e->getMessage());
    }
    if (PHP_SAPI === 'cli') {
        fwrite(STDERR, $e->getMessage() . PHP_EOL);
        exit(1);
    }
    calllog_json_response(400, ['ok' => false, 'error' => $e->getMessage()]);
}
