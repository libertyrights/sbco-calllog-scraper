<?php

declare(strict_types=1);

const CALLLOG_PUBLIC_KEY_PATH = __DIR__ . '/calllog_upload_public.pem';
const CALLLOG_RUNTIME_DIR = __DIR__ . '/runtime/http-upload';
const CALLLOG_LOG_PATH = __DIR__ . '/runtime/http-upload/upload.log';
const CALLLOG_ARCHIVE_INDEX_NAME = 'calllog_archive_index.json';
const CALLLOG_ARCHIVE_NAME_PATTERN = '/^calllog-archive-\d{8}\.csv\.gz$/';
const CALLLOG_ALLOWED_REMOTE_NAMES = [
    'calllog.csv',
    'calllog.json',
    'calllog_arrest_index.json',
    'calllog_upload_meta.json',
    'death_index.csv',
    'all_records.json',
    'court_records.json',
    'record_lookup_results.json',
    'release_arrest_enrichment.json',
];

function calllog_json_response(int $status, array $payload): void
{
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    exit;
}

function calllog_ensure_dir(string $path): void
{
    if (!is_dir($path) && !mkdir($path, 0775, true) && !is_dir($path)) {
        throw new RuntimeException('Failed to create directory: ' . $path);
    }
}

function calllog_log(string $message): void
{
    calllog_ensure_dir(dirname(CALLLOG_LOG_PATH));
    $line = sprintf("[%s] %s\n", gmdate('Y-m-d H:i:s'), $message);
    file_put_contents(CALLLOG_LOG_PATH, $line, FILE_APPEND);
}

function calllog_verify_signature(string $manifestJson, string $signature): void
{
    if (!is_file(CALLLOG_PUBLIC_KEY_PATH)) {
        throw new RuntimeException('Missing public key');
    }
    if (!function_exists('openssl_verify')) {
        throw new RuntimeException('OpenSSL verify is unavailable');
    }

    $publicKey = openssl_pkey_get_public((string) file_get_contents(CALLLOG_PUBLIC_KEY_PATH));
    if ($publicKey === false) {
        throw new RuntimeException('Public key could not be loaded');
    }

    $signatureBytes = base64_decode(trim($signature), true);
    if ($signatureBytes === false) {
        throw new RuntimeException('Signature is not valid base64');
    }

    $verified = openssl_verify($manifestJson, $signatureBytes, $publicKey, OPENSSL_ALGO_SHA256);
    if ($verified !== 1) {
        throw new RuntimeException('Signature verification failed');
    }
}

function calllog_atomic_replace(string $tmpPath, string $finalPath): void
{
    calllog_ensure_dir(dirname($finalPath));
    $backupPath = $finalPath . '.bak';
    if (is_file($finalPath) && filesize($finalPath) !== false && filesize($tmpPath) !== false && filesize($finalPath) > filesize($tmpPath)) {
        $stamp = gmdate('Ymd\THis\Z');
        @copy($finalPath, $finalPath . '.larger-before-override.' . $stamp . '.bak');
    }
    if (is_file($backupPath)) {
        @unlink($backupPath);
    }
    if (is_file($finalPath)) {
        @rename($finalPath, $backupPath);
    }
    if (!@rename($tmpPath, $finalPath)) {
        throw new RuntimeException('Failed to publish file: ' . basename($finalPath));
    }
}

function calllog_resolve_final_path(string $remoteName): string
{
    $allowedNames = array_flip(CALLLOG_ALLOWED_REMOTE_NAMES);
    if (isset($allowedNames[$remoteName])) {
        return __DIR__ . DIRECTORY_SEPARATOR . $remoteName;
    }
    if ($remoteName === CALLLOG_ARCHIVE_INDEX_NAME || preg_match(CALLLOG_ARCHIVE_NAME_PATTERN, $remoteName) === 1) {
        return __DIR__ . DIRECTORY_SEPARATOR . $remoteName;
    }
    throw new RuntimeException('Remote file name is not allowed: ' . $remoteName);
}

function calllog_build_batch_id(array $manifest): string
{
    $timestamp = preg_replace('/[^0-9TZ]/', '', (string) ($manifest['batch_timestamp'] ?? 'unknown'));
    $runId = preg_replace('/[^A-Za-z0-9._-]+/', '-', (string) ($manifest['run_id'] ?? 'run'));
    return trim($timestamp . '-' . $runId, '-');
}

try {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
        calllog_json_response(405, ['ok' => false, 'error' => 'POST required']);
    }

    $manifestJson = (string) ($_POST['manifest'] ?? '');
    $signature = (string) ($_POST['signature'] ?? '');
    if ($manifestJson === '' || $signature === '') {
        throw new RuntimeException('Missing manifest or signature');
    }

    calllog_verify_signature($manifestJson, $signature);

    $manifest = json_decode($manifestJson, true);
    if (!is_array($manifest) || empty($manifest['files']) || !is_array($manifest['files'])) {
        throw new RuntimeException('Manifest is invalid');
    }

    $batchId = calllog_build_batch_id($manifest);
    calllog_ensure_dir(CALLLOG_RUNTIME_DIR);

    foreach ($manifest['files'] as $item) {
        if (!is_array($item)) {
            throw new RuntimeException('Manifest file entry is invalid');
        }

        $fieldName = (string) ($item['field_name'] ?? '');
        $remoteName = (string) ($item['remote_name'] ?? '');
        $expectedSha = strtolower((string) ($item['sha256'] ?? ''));
        $expectedSize = (int) ($item['size'] ?? -1);

        if ($fieldName === '' || $remoteName === '' || $expectedSha === '' || $expectedSize < 0) {
            throw new RuntimeException('Manifest file entry is incomplete');
        }
        if (!isset($_FILES[$fieldName])) {
            throw new RuntimeException('Missing uploaded file: ' . $fieldName);
        }

        $upload = $_FILES[$fieldName];
        if (!is_uploaded_file($upload['tmp_name'] ?? '')) {
            throw new RuntimeException('Upload transport failed for ' . $remoteName);
        }
        if ((int) ($upload['error'] ?? UPLOAD_ERR_OK) !== UPLOAD_ERR_OK) {
            throw new RuntimeException('Upload error for ' . $remoteName);
        }

        $safeRemoteName = preg_replace('/[^A-Za-z0-9._-]+/', '_', $remoteName);
        $tmpPath = CALLLOG_RUNTIME_DIR . DIRECTORY_SEPARATOR . $batchId . '.' . $safeRemoteName . '.part';
        $finalPath = calllog_resolve_final_path($remoteName);
        if (!move_uploaded_file($upload['tmp_name'], $tmpPath)) {
            throw new RuntimeException('Failed to store uploaded file: ' . $remoteName);
        }

        $actualSize = filesize($tmpPath);
        $actualSha = strtolower((string) hash_file('sha256', $tmpPath));
        if ($actualSize !== $expectedSize || $actualSha !== $expectedSha) {
            @unlink($tmpPath);
            throw new RuntimeException('Uploaded file verification failed: ' . $remoteName);
        }

        calllog_atomic_replace($tmpPath, $finalPath);
    }

    calllog_log(
        'Published signed batch ' . $batchId
        . ' source=' . (string) ($manifest['source'] ?? '')
        . ' run_id=' . (string) ($manifest['run_id'] ?? '')
        . ' publisher=' . (string) ($manifest['publisher'] ?? '')
    );

    calllog_json_response(200, [
        'ok' => true,
        'batch_id' => $batchId,
        'published' => true,
    ]);
} catch (Throwable $e) {
    calllog_log('Upload error: ' . $e->getMessage());
    calllog_json_response(400, ['ok' => false, 'error' => $e->getMessage()]);
}
