<?php

declare(strict_types=1);

const CALLLOG_TRIGGER_DEFAULT_CONFIG = __DIR__ . '/../../calllog_github_trigger_config.php';
const CALLLOG_TRIGGER_RUNTIME_DIR = __DIR__ . '/runtime/github-trigger';
const CALLLOG_TRIGGER_STATE_PATH = CALLLOG_TRIGGER_RUNTIME_DIR . '/state.json';
const CALLLOG_TRIGGER_LOCK_PATH = CALLLOG_TRIGGER_RUNTIME_DIR . '/trigger.lock';
const CALLLOG_TRIGGER_LOG_PATH = CALLLOG_TRIGGER_RUNTIME_DIR . '/trigger.log';
const CALLLOG_UPLOAD_META_PATH = __DIR__ . '/calllog_upload_meta.json';

function calllog_trigger_is_cli(): bool
{
    return PHP_SAPI === 'cli';
}

function calllog_trigger_response(int $status, array $payload): void
{
    if (!calllog_trigger_is_cli()) {
        http_response_code($status);
        header('Content-Type: application/json');
    }
    echo json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL;
    exit($status >= 400 ? 1 : 0);
}

function calllog_trigger_ensure_dir(string $path): void
{
    if (!is_dir($path) && !mkdir($path, 0775, true) && !is_dir($path)) {
        throw new RuntimeException('Failed to create directory: ' . $path);
    }
}

function calllog_trigger_log(string $message): void
{
    calllog_trigger_ensure_dir(CALLLOG_TRIGGER_RUNTIME_DIR);
    file_put_contents(
        CALLLOG_TRIGGER_LOG_PATH,
        sprintf("[%s] %s\n", gmdate('Y-m-d H:i:s'), $message),
        FILE_APPEND
    );
}

function calllog_trigger_load_config(): array
{
    $configPath = getenv('SBCO_TRIGGER_CONFIG') ?: CALLLOG_TRIGGER_DEFAULT_CONFIG;
    if (!is_file($configPath)) {
        throw new RuntimeException('Missing trigger config: ' . $configPath);
    }

    $config = require $configPath;
    if (!is_array($config)) {
        throw new RuntimeException('Trigger config must return an array');
    }

    $defaults = [
        'owner' => 'libertyrights',
        'repo' => 'sbco-calllog-scraper',
        'workflow' => 'sbco-calllog.yml',
        'ref' => 'main',
        'github_token' => '',
        'trigger_token' => '',
        'dispatch_if_upload_older_than_seconds' => 18 * 60,
        'min_seconds_between_dispatches' => 15 * 60,
    ];

    return array_merge($defaults, $config);
}

function calllog_trigger_request_value(string $name): string
{
    if (calllog_trigger_is_cli()) {
        global $argv;
        foreach ($argv ?? [] as $arg) {
            if (strpos($arg, '--' . $name . '=') === 0) {
                return substr($arg, strlen($name) + 3);
            }
            if ($arg === '--' . $name) {
                return '1';
            }
        }
        return '';
    }

    return (string) ($_POST[$name] ?? $_GET[$name] ?? '');
}

function calllog_trigger_authorize_http(array $config): void
{
    if (calllog_trigger_is_cli()) {
        return;
    }

    $expected = (string) ($config['trigger_token'] ?? '');
    if ($expected === '') {
        throw new RuntimeException('HTTP trigger token is not configured');
    }

    $provided = calllog_trigger_request_value('token');
    if (!hash_equals($expected, $provided)) {
        calllog_trigger_response(403, ['ok' => false, 'error' => 'Forbidden']);
    }
}

function calllog_trigger_read_json_file(string $path): array
{
    if (!is_file($path)) {
        return [];
    }

    $decoded = json_decode((string) file_get_contents($path), true);
    return is_array($decoded) ? $decoded : [];
}

function calllog_trigger_write_state(array $state): void
{
    calllog_trigger_ensure_dir(CALLLOG_TRIGGER_RUNTIME_DIR);
    file_put_contents(
        CALLLOG_TRIGGER_STATE_PATH,
        json_encode($state, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)
    );
}

function calllog_trigger_timestamp(?string $value): int
{
    if ($value === null || trim($value) === '') {
        return 0;
    }

    $timestamp = strtotime($value);
    return $timestamp === false ? 0 : $timestamp;
}

function calllog_trigger_get_upload_timestamp(): int
{
    $meta = calllog_trigger_read_json_file(CALLLOG_UPLOAD_META_PATH);
    return calllog_trigger_timestamp(
        (string) ($meta['generated_at'] ?? $meta['uploaded_at'] ?? $meta['timestamp'] ?? '')
    );
}

function calllog_trigger_should_dispatch(array $config, array $state): array
{
    $now = time();
    $force = calllog_trigger_request_value('force') === '1';
    $lastDispatchAt = calllog_trigger_timestamp((string) ($state['last_dispatch_at'] ?? ''));
    $lastUploadAt = calllog_trigger_get_upload_timestamp();
    $minDispatchGap = max(0, (int) ($config['min_seconds_between_dispatches'] ?? 900));
    $uploadStaleAfter = max(0, (int) ($config['dispatch_if_upload_older_than_seconds'] ?? 1080));

    if (!$force && $lastDispatchAt > 0 && ($now - $lastDispatchAt) < $minDispatchGap) {
        return [
            'dispatch' => false,
            'reason' => 'last_dispatch_too_recent',
            'last_dispatch_age_seconds' => $now - $lastDispatchAt,
        ];
    }

    if (!$force && $lastUploadAt > 0 && ($now - $lastUploadAt) < $uploadStaleAfter) {
        return [
            'dispatch' => false,
            'reason' => 'upload_not_stale',
            'last_upload_age_seconds' => $now - $lastUploadAt,
        ];
    }

    return [
        'dispatch' => true,
        'reason' => $force ? 'forced' : 'upload_stale_or_missing',
        'last_upload_age_seconds' => $lastUploadAt > 0 ? $now - $lastUploadAt : null,
    ];
}

function calllog_trigger_dispatch_github(array $config): array
{
    $token = trim((string) ($config['github_token'] ?? ''));
    if ($token === '') {
        throw new RuntimeException('GitHub token is not configured');
    }
    if (!function_exists('curl_init')) {
        throw new RuntimeException('PHP cURL extension is unavailable');
    }

    $owner = rawurlencode((string) $config['owner']);
    $repo = rawurlencode((string) $config['repo']);
    $workflow = rawurlencode((string) $config['workflow']);
    $url = "https://api.github.com/repos/{$owner}/{$repo}/actions/workflows/{$workflow}/dispatches";
    $body = json_encode(['ref' => (string) $config['ref']], JSON_UNESCAPED_SLASHES);

    $curl = curl_init($url);
    if ($curl === false) {
        throw new RuntimeException('Failed to initialize cURL');
    }

    curl_setopt_array($curl, [
        CURLOPT_CUSTOMREQUEST => 'POST',
        CURLOPT_POSTFIELDS => $body,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_HTTPHEADER => [
            'Accept: application/vnd.github+json',
            'Authorization: Bearer ' . $token,
            'Content-Type: application/json',
            'User-Agent: sbco-calllog-serv00-trigger',
            'X-GitHub-Api-Version: 2022-11-28',
        ],
    ]);

    $raw = curl_exec($curl);
    $error = curl_error($curl);
    $status = (int) curl_getinfo($curl, CURLINFO_HTTP_CODE);
    $headerSize = (int) curl_getinfo($curl, CURLINFO_HEADER_SIZE);
    curl_close($curl);

    if ($raw === false) {
        throw new RuntimeException('GitHub dispatch request failed: ' . $error);
    }

    $responseBody = substr((string) $raw, $headerSize);
    if ($status < 200 || $status >= 300) {
        throw new RuntimeException('GitHub dispatch returned HTTP ' . $status . ': ' . trim($responseBody));
    }

    return [
        'github_status' => $status,
        'workflow' => (string) $config['workflow'],
        'ref' => (string) $config['ref'],
    ];
}

try {
    $config = calllog_trigger_load_config();
    calllog_trigger_authorize_http($config);
    calllog_trigger_ensure_dir(CALLLOG_TRIGGER_RUNTIME_DIR);

    $lock = fopen(CALLLOG_TRIGGER_LOCK_PATH, 'c');
    if ($lock === false) {
        throw new RuntimeException('Failed to open trigger lock');
    }
    if (!flock($lock, LOCK_EX | LOCK_NB)) {
        calllog_trigger_response(200, ['ok' => true, 'dispatched' => false, 'reason' => 'already_running']);
    }

    $state = calllog_trigger_read_json_file(CALLLOG_TRIGGER_STATE_PATH);
    $decision = calllog_trigger_should_dispatch($config, $state);
    if (!$decision['dispatch']) {
        $state['last_checked_at'] = gmdate('c');
        $state['last_skip_reason'] = $decision['reason'];
        calllog_trigger_write_state($state);
        calllog_trigger_response(200, ['ok' => true, 'dispatched' => false] + $decision);
    }

    $dispatch = calllog_trigger_dispatch_github($config);
    $state = array_merge($state, [
        'last_checked_at' => gmdate('c'),
        'last_dispatch_at' => gmdate('c'),
        'last_dispatch_reason' => $decision['reason'],
        'last_error' => null,
    ], $dispatch);
    calllog_trigger_write_state($state);
    calllog_trigger_log('Dispatched GitHub workflow reason=' . $decision['reason']);

    calllog_trigger_response(200, ['ok' => true, 'dispatched' => true] + $decision + $dispatch);
} catch (Throwable $e) {
    $state = calllog_trigger_read_json_file(CALLLOG_TRIGGER_STATE_PATH);
    $state['last_checked_at'] = gmdate('c');
    $state['last_error'] = $e->getMessage();
    calllog_trigger_write_state($state);
    calllog_trigger_log('Error: ' . $e->getMessage());
    calllog_trigger_response(500, ['ok' => false, 'error' => $e->getMessage()]);
}

