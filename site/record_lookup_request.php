<?php

declare(strict_types=1);

const RECORD_LOOKUP_QUEUE_DIR = __DIR__ . '/runtime/record-lookup';
const RECORD_LOOKUP_QUEUE_PATH = RECORD_LOOKUP_QUEUE_DIR . '/record_lookup_queue.json';
const RECORD_LOOKUP_MAX_QUEUE_ITEMS = 250;

function record_lookup_response(int $status, array $payload): void
{
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    exit;
}

function record_lookup_read_input(): array
{
    $raw = (string) file_get_contents('php://input');
    $payload = json_decode($raw, true);
    if (!is_array($payload)) {
        $payload = $_POST;
    }
    return is_array($payload) ? $payload : [];
}

function record_lookup_clean_string($value, int $limit = 220): string
{
    $text = trim(preg_replace('/\s+/', ' ', (string) $value) ?? '');
    $text = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]+/', '', $text) ?? $text;
    return substr($text, 0, $limit);
}

function record_lookup_clean_kind($value): string
{
    $kind = strtolower(record_lookup_clean_string($value, 40));
    if (in_array($kind, ['lcn', 'arrest', 'release'], true)) {
        return 'lcn';
    }
    if (in_array($kind, ['death', 'death_index', 'coroner'], true)) {
        return 'death';
    }
    if (in_array($kind, ['court', 'case'], true)) {
        return 'court';
    }
    return 'lcn';
}

function record_lookup_read_queue(): array
{
    if (!is_file(RECORD_LOOKUP_QUEUE_PATH)) {
        return ['updated_at' => gmdate('c'), 'requests' => []];
    }
    $payload = json_decode((string) file_get_contents(RECORD_LOOKUP_QUEUE_PATH), true);
    if (!is_array($payload)) {
        return ['updated_at' => gmdate('c'), 'requests' => []];
    }
    if (!isset($payload['requests']) || !is_array($payload['requests'])) {
        $payload['requests'] = [];
    }
    return $payload;
}

function record_lookup_ensure_dir(string $path): void
{
    if (!is_dir($path) && !mkdir($path, 0775, true) && !is_dir($path)) {
        throw new RuntimeException('Failed to create queue directory');
    }
}

function record_lookup_write_queue(array $queue): void
{
    record_lookup_ensure_dir(RECORD_LOOKUP_QUEUE_DIR);
    $queue['updated_at'] = gmdate('c');
    $queue['requests'] = array_slice(array_values($queue['requests'] ?? []), -RECORD_LOOKUP_MAX_QUEUE_ITEMS);
    $tmpPath = RECORD_LOOKUP_QUEUE_PATH . '.tmp';
    file_put_contents($tmpPath, json_encode($queue, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
    if (!@rename($tmpPath, RECORD_LOOKUP_QUEUE_PATH)) {
        @unlink($tmpPath);
        throw new RuntimeException('Failed to save lookup queue');
    }
}

function record_lookup_key(array $item): string
{
    return implode('|', [
        strtolower((string) ($item['kind'] ?? '')),
        strtolower((string) ($item['query'] ?? '')),
        strtolower((string) ($item['record_key'] ?? '')),
    ]);
}

try {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
        record_lookup_response(405, ['ok' => false, 'error' => 'POST required']);
    }

    $input = record_lookup_read_input();
    $kind = record_lookup_clean_kind($input['kind'] ?? '');
    $query = record_lookup_clean_string($input['query'] ?? '');
    if ($query === '') {
        record_lookup_response(400, ['ok' => false, 'error' => 'Missing lookup query']);
    }

    $record = $input['record'] ?? [];
    $recordKey = '';
    if (is_array($record)) {
        $recordKey = record_lookup_clean_string($record['id'] ?? $record['arrest_id'] ?? $record['caseNumberDisplay'] ?? $record['Name'] ?? '', 160);
    }

    $item = [
        'id' => bin2hex(random_bytes(8)),
        'kind' => $kind,
        'query' => $query,
        'record_key' => $recordKey,
        'requested_at' => gmdate('c'),
        'status' => 'queued',
        'source' => 'records.html',
    ];

    $queue = record_lookup_read_queue();
    $existingKeys = [];
    foreach ($queue['requests'] as $queued) {
        if (is_array($queued)) {
            $existingKeys[record_lookup_key($queued)] = true;
        }
    }

    $duplicate = isset($existingKeys[record_lookup_key($item)]);
    if (!$duplicate) {
        $queue['requests'][] = $item;
        record_lookup_write_queue($queue);
    }

    record_lookup_response(200, [
        'ok' => true,
        'queued' => !$duplicate,
        'duplicate' => $duplicate,
        'message' => $duplicate ? 'Lookup request was already queued.' : 'Lookup request queued.',
        'dispatched' => false,
        'trigger' => [
            'reason' => 'queued_for_next_automation_run',
        ],
    ]);
} catch (Throwable $e) {
    record_lookup_response(500, ['ok' => false, 'error' => $e->getMessage()]);
}
