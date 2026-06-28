<?php

function calllog_load_config(): array
{
    $path = __DIR__ . '/calllog_server_config.php';
    if (!is_file($path)) {
        throw new RuntimeException('Missing calllog_server_config.php');
    }
    $config = require $path;
    if (!is_array($config)) {
        throw new RuntimeException('Invalid calllog_server_config.php');
    }
    return $config;
}

function calllog_ensure_dir(string $path): void
{
    if (!is_dir($path) && !mkdir($path, 0775, true) && !is_dir($path)) {
        throw new RuntimeException('Failed to create directory: ' . $path);
    }
}

function calllog_log(array $config, string $message): void
{
    $path = $config['log_path'] ?? '';
    if (!$path) {
        return;
    }
    calllog_ensure_dir(dirname($path));
    $line = sprintf("[%s] %s\n", gmdate('Y-m-d H:i:s'), $message);
    file_put_contents($path, $line, FILE_APPEND);
}

function calllog_json_response(int $status, array $payload): void
{
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    exit;
}

function calllog_atomic_write(string $path, string $contents): void
{
    $tmp = $path . '.' . uniqid('tmp', true);
    file_put_contents($tmp, $contents);
    rename($tmp, $path);
}

function calllog_upload_trace_path(array $config): string
{
    return rtrim((string) $config['live_dir'], DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . 'calllog_upload_meta.json';
}

function calllog_signature_for_manifest(string $manifestJson, string $secret): string
{
    return hash_hmac('sha256', $manifestJson, $secret);
}

function calllog_safe_name(string $value): string
{
    $value = preg_replace('/[^A-Za-z0-9._-]+/', '-', $value);
    return trim((string) $value, '-');
}

function calllog_batch_timestamp(string $timestampText): DateTimeImmutable
{
    $value = DateTimeImmutable::createFromFormat('Y-m-d\TH:i:s\Z', $timestampText, new DateTimeZone('UTC'));
    if (!$value) {
        throw new RuntimeException('Invalid batch timestamp');
    }
    return $value;
}

function calllog_upload_window_allowed(array $config, DateTimeImmutable $instant): bool
{
    $window = $config['accepted_upload_window_utc'] ?? null;
    if (!is_array($window) || empty($window['enabled'])) {
        return true;
    }

    $hour = (int) $instant->format('G');
    $minute = (int) $instant->format('i');
    $allowedHours = $window['allowed_hours'] ?? null;
    $allowedMinutes = $window['allowed_minutes'] ?? null;

    if (is_array($allowedHours) && $allowedHours) {
        $allowedHours = array_map(
            static fn($value): int => max(0, min(23, (int) $value)),
            $allowedHours
        );
        if (!in_array($hour, $allowedHours, true)) {
            return false;
        }
    } else {
        $hourModulo = max(1, (int) ($window['hour_modulo'] ?? 4));
        $hourOffset = (int) ($window['hour_offset'] ?? 0);
        if (($hour - $hourOffset) % $hourModulo !== 0) {
            return false;
        }
    }

    if (is_array($allowedMinutes) && $allowedMinutes) {
        $allowedMinutes = array_map(
            static fn($value): int => max(0, min(59, (int) $value)),
            $allowedMinutes
        );
        return in_array($minute, $allowedMinutes, true);
    }

    $minuteStart = max(0, min(59, (int) ($window['minute_start'] ?? 0)));
    $minuteEnd = max($minuteStart, min(59, (int) ($window['minute_end'] ?? 59)));
    return $minute >= $minuteStart && $minute <= $minuteEnd;
}

function calllog_validate_manifest(array $config, string $manifestJson, string $signature): array
{
    $secret = (string) ($config['upload_secret'] ?? '');
    if ($secret === '') {
        throw new RuntimeException('Upload secret is not configured');
    }

    $expected = calllog_signature_for_manifest($manifestJson, $secret);
    if (!hash_equals($expected, strtolower(trim($signature)))) {
        throw new RuntimeException('Invalid upload signature');
    }

    $manifest = json_decode($manifestJson, true);
    if (!is_array($manifest)) {
        throw new RuntimeException('Manifest is not valid JSON');
    }

    $batchTimestamp = (string) ($manifest['batch_timestamp'] ?? '');
    $runId = (string) ($manifest['run_id'] ?? '');
    $source = (string) ($manifest['source'] ?? '');
    $files = $manifest['files'] ?? null;

    if ($batchTimestamp === '' || $runId === '' || $source === '' || !is_array($files) || !$files) {
        throw new RuntimeException('Manifest is missing required fields');
    }

    $batchTs = calllog_batch_timestamp($batchTimestamp);
    $age = time() - $batchTs->getTimestamp();
    $maxAge = max(60, (int) ($config['upload_max_age_seconds'] ?? 7200));
    if ($age < -300 || $age > $maxAge) {
        throw new RuntimeException('Upload timestamp is outside the accepted age window');
    }
    $receivedAt = new DateTimeImmutable('now', new DateTimeZone('UTC'));
    if (!calllog_upload_window_allowed($config, $receivedAt)) {
        throw new RuntimeException('Upload is outside the accepted UTC upload window');
    }

    $allowedNames = array_flip($config['allowed_remote_names'] ?? []);
    foreach ($files as $item) {
        if (!is_array($item)) {
            throw new RuntimeException('Manifest file entry is invalid');
        }
        $fieldName = (string) ($item['field_name'] ?? '');
        $remoteName = (string) ($item['remote_name'] ?? '');
        $sha256 = strtolower((string) ($item['sha256'] ?? ''));
        $size = (int) ($item['size'] ?? -1);
        if ($fieldName === '' || $remoteName === '' || $sha256 === '' || $size < 0) {
            throw new RuntimeException('Manifest file entry is incomplete');
        }
        if (!isset($allowedNames[$remoteName])) {
            throw new RuntimeException('Remote file name is not allowed: ' . $remoteName);
        }
    }

    return $manifest;
}

function calllog_build_batch_id(array $manifest): string
{
    $ts = calllog_batch_timestamp((string) $manifest['batch_timestamp'])->format('Ymd\THis\Z');
    $run = calllog_safe_name((string) ($manifest['run_id'] ?? 'run'));
    return $ts . '-' . $run;
}

function calllog_delete_tree(string $path): void
{
    if (!file_exists($path)) {
        return;
    }
    if (is_file($path) || is_link($path)) {
        @unlink($path);
        return;
    }
    $items = scandir($path);
    foreach ($items ?: [] as $item) {
        if ($item === '.' || $item === '..') {
            continue;
        }
        calllog_delete_tree($path . DIRECTORY_SEPARATOR . $item);
    }
    @rmdir($path);
}

function calllog_store_upload_batch(array $config, array $manifest, array $files): array
{
    $batchId = calllog_build_batch_id($manifest);
    $incomingDir = rtrim((string) $config['incoming_dir'], DIRECTORY_SEPARATOR);
    $batchDir = $incomingDir . DIRECTORY_SEPARATOR . $batchId;

    calllog_ensure_dir($incomingDir);
    if (file_exists($batchDir)) {
        throw new RuntimeException('Batch already exists: ' . $batchId);
    }
    calllog_ensure_dir($batchDir);

    foreach ($manifest['files'] as $item) {
        $fieldName = (string) $item['field_name'];
        $remoteName = (string) $item['remote_name'];
        if (!isset($files[$fieldName])) {
            throw new RuntimeException('Missing uploaded file field: ' . $fieldName);
        }
        $upload = $files[$fieldName];
        if (!is_uploaded_file($upload['tmp_name'] ?? '')) {
            throw new RuntimeException('Uploaded file was not received correctly: ' . $fieldName);
        }
        if ((int) ($upload['error'] ?? UPLOAD_ERR_OK) !== UPLOAD_ERR_OK) {
            throw new RuntimeException('Upload error for ' . $fieldName);
        }

        $tmpPath = $batchDir . DIRECTORY_SEPARATOR . $remoteName . '.part';
        $finalPath = $batchDir . DIRECTORY_SEPARATOR . $remoteName;
        if (!move_uploaded_file($upload['tmp_name'], $tmpPath)) {
            throw new RuntimeException('Failed to store uploaded file: ' . $remoteName);
        }

        $actualSize = filesize($tmpPath);
        $actualSha = hash_file('sha256', $tmpPath);
        if ($actualSize !== (int) $item['size'] || strtolower($actualSha) !== strtolower((string) $item['sha256'])) {
            @unlink($tmpPath);
            throw new RuntimeException('Uploaded file verification failed: ' . $remoteName);
        }
        rename($tmpPath, $finalPath);
    }

    calllog_atomic_write(
        $batchDir . DIRECTORY_SEPARATOR . 'manifest.json',
        json_encode($manifest, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)
    );
    calllog_atomic_write(
        $batchDir . DIRECTORY_SEPARATOR . 'received.json',
        json_encode(
            [
                'received_at' => gmdate('Y-m-d\TH:i:s\Z'),
                'remote_addr' => $_SERVER['REMOTE_ADDR'] ?? '',
                'user_agent' => $_SERVER['HTTP_USER_AGENT'] ?? '',
            ],
            JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES
        )
    );
    calllog_atomic_write($batchDir . DIRECTORY_SEPARATOR . 'complete.json', json_encode(['ok' => true]));

    calllog_log(
        $config,
        'Stored upload batch ' . $batchId
        . ' source=' . ((string) ($manifest['source'] ?? ''))
        . ' run_id=' . ((string) ($manifest['run_id'] ?? ''))
        . ' publisher=' . ((string) ($manifest['publisher'] ?? ''))
    );
    return [
        'batch_id' => $batchId,
        'batch_dir' => $batchDir,
    ];
}

function calllog_promote_file(string $sourcePath, string $destPath): void
{
    $destDir = dirname($destPath);
    calllog_ensure_dir($destDir);

    $tmpPath = $destPath . '.' . gmdate('Ymd\THis\Z') . '.new';
    $bakPath = $destPath . '.bak';

    if (!copy($sourcePath, $tmpPath)) {
        throw new RuntimeException('Failed to copy temp file into place: ' . basename($destPath));
    }
    if (file_exists($bakPath)) {
        @unlink($bakPath);
    }
    if (file_exists($destPath)) {
        @rename($destPath, $bakPath);
    }
    if (!@rename($tmpPath, $destPath)) {
        @unlink($tmpPath);
        throw new RuntimeException('Failed to promote live file: ' . basename($destPath));
    }
}

function calllog_trigger_rebuild(array $config): ?array
{
    $url = (string) ($config['rebuild_url'] ?? '');
    $token = (string) ($config['rebuild_token'] ?? '');
    if ($url === '' || $token === '') {
        return null;
    }
    $target = $url . (str_contains($url, '?') ? '&' : '?') . 'token=' . rawurlencode($token);
    $body = @file_get_contents($target);
    return [
        'url' => $target,
        'response' => $body === false ? '' : trim($body),
    ];
}

function calllog_write_upload_trace(array $config, array $manifest): void
{
    $payload = [
        'processed_at' => gmdate('c'),
        'batch_timestamp' => (string) ($manifest['batch_timestamp'] ?? ''),
        'batch_id' => calllog_build_batch_id($manifest),
        'source' => (string) ($manifest['source'] ?? ''),
        'run_id' => (string) ($manifest['run_id'] ?? ''),
        'publisher' => (string) ($manifest['publisher'] ?? ''),
    ];
    calllog_atomic_write(
        calllog_upload_trace_path($config),
        json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)
    );
}

function calllog_process_queue(array $config): array
{
    $queueRoot = (string) $config['queue_root'];
    $incomingDir = (string) $config['incoming_dir'];
    $liveDir = (string) $config['live_dir'];

    calllog_ensure_dir($queueRoot);
    calllog_ensure_dir($incomingDir);
    calllog_ensure_dir($liveDir);

    $lockPath = $queueRoot . DIRECTORY_SEPARATOR . 'process.lock';
    $lockHandle = fopen($lockPath, 'c+');
    if (!$lockHandle) {
        throw new RuntimeException('Unable to open process lock');
    }
    if (!flock($lockHandle, LOCK_EX | LOCK_NB)) {
        fclose($lockHandle);
        return [
            'ok' => false,
            'status' => 'busy',
            'message' => 'Processor lock is already held',
        ];
    }

    try {
        $batches = [];
        foreach (scandir($incomingDir) ?: [] as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $batchDir = $incomingDir . DIRECTORY_SEPARATOR . $entry;
            if (!is_dir($batchDir) || !is_file($batchDir . DIRECTORY_SEPARATOR . 'complete.json')) {
                continue;
            }
            $manifestPath = $batchDir . DIRECTORY_SEPARATOR . 'manifest.json';
            if (!is_file($manifestPath)) {
                continue;
            }
            $manifest = json_decode((string) file_get_contents($manifestPath), true);
            if (!is_array($manifest) || empty($manifest['batch_timestamp'])) {
                continue;
            }
            $batches[] = [
                'batch_dir' => $batchDir,
                'manifest' => $manifest,
                'sort_key' => (string) $manifest['batch_timestamp'],
            ];
        }

        usort(
            $batches,
            static fn(array $a, array $b): int => strcmp($a['sort_key'], $b['sort_key'])
        );

        $processed = [];
        $requiredNames = array_flip($config['required_remote_names'] ?? []);
        $rebuildTriggered = null;

        foreach ($batches as $batch) {
            $manifest = $batch['manifest'];
            $batchDir = $batch['batch_dir'];
            $seenRemoteNames = [];
            foreach ($manifest['files'] as $item) {
                $remoteName = (string) $item['remote_name'];
                $sourcePath = $batchDir . DIRECTORY_SEPARATOR . $remoteName;
                if (!is_file($sourcePath)) {
                    throw new RuntimeException('Queued file is missing: ' . $remoteName);
                }
                calllog_promote_file($sourcePath, rtrim($liveDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $remoteName);
                $seenRemoteNames[$remoteName] = true;
            }
            foreach ($requiredNames as $remoteName => $_required) {
                if (!isset($seenRemoteNames[$remoteName])) {
                    throw new RuntimeException('Batch is missing required remote file: ' . $remoteName);
                }
            }

            $processed[] = [
                'batch_id' => calllog_build_batch_id($manifest),
                'file_count' => count($manifest['files']),
                'source' => (string) ($manifest['source'] ?? ''),
                'run_id' => (string) ($manifest['run_id'] ?? ''),
                'publisher' => (string) ($manifest['publisher'] ?? ''),
            ];
            calllog_write_upload_trace($config, $manifest);
            calllog_delete_tree($batchDir);
            $processedEntry = end($processed);
            calllog_log(
                $config,
                'Processed upload batch ' . $processedEntry['batch_id']
                . ' source=' . $processedEntry['source']
                . ' run_id=' . $processedEntry['run_id']
                . ' publisher=' . $processedEntry['publisher']
            );
        }

        if ($processed) {
            $rebuildTriggered = calllog_trigger_rebuild($config);
        }

        return [
            'ok' => true,
            'status' => 'processed',
            'processed_batches' => $processed,
            'rebuild' => $rebuildTriggered,
        ];
    } finally {
        flock($lockHandle, LOCK_UN);
        fclose($lockHandle);
    }
}
