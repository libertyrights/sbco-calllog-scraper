<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function read_json_description_file(string $path): ?array
{
    if (!is_file($path) || !is_readable($path)) {
        return null;
    }
    $raw = file_get_contents($path);
    if ($raw === false || trim($raw) === '') {
        return null;
    }
    $payload = json_decode($raw, true);
    return is_array($payload) ? $payload : null;
}

function normalize_header(string $value): string
{
    return strtolower(preg_replace('/[^a-z0-9]+/', '', trim($value)) ?? '');
}

function csv_value(array $row, array $headers, array $names): string
{
    foreach ($names as $name) {
        $key = normalize_header($name);
        if (isset($headers[$key])) {
            $value = trim((string) ($row[$headers[$key]] ?? ''));
            if ($value !== '') {
                return $value;
            }
        }
    }
    return '';
}

function description_section(string $value, ?string $default): string
{
    $key = normalize_header($value);
    if ($key === '' && $default !== null) {
        $key = normalize_header($default);
    }
    if (in_array($key, ['prefix', 'prefixes', 'callprefix', 'callprefixes'], true)) {
        return 'prefixes';
    }
    if (in_array($key, ['dispo', 'disposition', 'dispositions', 'dispositioncode'], true)) {
        return 'dispositions';
    }
    if (in_array($key, ['calltype', 'calltypes', 'type', 'types', 'call', 'calls'], true)) {
        return 'call_types';
    }
    return '';
}

function read_csv_description_file(string $path, ?string $default_section = null): ?array
{
    if (!is_file($path) || !is_readable($path)) {
        return null;
    }
    $handle = fopen($path, 'r');
    if ($handle === false) {
        return null;
    }

    $payload = ['prefixes' => [], 'dispositions' => [], 'call_types' => []];
    $header = fgetcsv($handle);
    if ($header === false) {
        fclose($handle);
        return null;
    }

    $headers = [];
    foreach ($header as $index => $name) {
        $headers[normalize_header((string) $name)] = $index;
    }
    $hasNamedColumns = count(array_intersect(array_keys($headers), [
        'category', 'section', 'kind', 'map', 'list', 'group',
        'code', 'key', 'calltype', 'type', 'dispo', 'disposition', 'prefix',
        'description', 'desc', 'label', 'meaning', 'text',
    ])) > 0;

    $rows = [];
    if ($hasNamedColumns) {
        $rows[] = $header;
    } else {
        $headers = ['code' => 0, 'description' => 1];
    }
    while (($row = fgetcsv($handle)) !== false) {
        $rows[] = $row;
    }
    fclose($handle);

    foreach ($rows as $row) {
        $section = description_section(
            csv_value($row, $headers, ['category', 'section', 'kind', 'map', 'list', 'group']),
            $default_section
        );
        if ($section === '') {
            $section = description_section('', $default_section);
        }
        if ($section === '') {
            continue;
        }

        $code = csv_value($row, $headers, ['code', 'key', 'call type', 'call_type', 'type', 'dispo', 'disposition', 'prefix']);
        $description = csv_value($row, $headers, ['description', 'desc', 'label', 'meaning', 'text']);
        if ($description === '' && count($row) >= 2) {
            $description = trim((string) $row[count($row) - 1]);
        }
        if ($code !== '' && $description !== '') {
            $payload[$section][$code] = $description;
        }
    }

    return $payload;
}

function clean_map($value): array
{
    if (!is_array($value)) {
        return [];
    }
    $out = [];
    foreach ($value as $key => $description) {
        $code = strtoupper(trim((string) $key));
        $text = trim((string) $description);
        if ($code !== '' && $text !== '') {
            $out[$code] = $text;
        }
    }
    ksort($out);
    return $out;
}

function normalize_descriptions(array $payload): array
{
    return [
        'prefixes' => clean_map($payload['prefixes'] ?? []),
        'dispositions' => clean_map($payload['dispositions'] ?? []),
        'call_types' => clean_map($payload['call_types'] ?? []),
    ];
}

$fallback = [
    'prefixes' => [
        'CHP' => 'California Highway Patrol incident',
        'CO' => 'Coroner call',
        'CS' => 'Court Services call',
        'SBCFIRE' => 'San Bernardino County Fire / EMS incident',
    ],
    'dispositions' => [
        '*' => 'Open/no final disposition yet',
        'ABA' => 'Arrest by agency',
        'ACT' => 'Active',
        'ARR' => 'Arrest',
        'CAN' => 'Cancelled',
        'CIA' => 'Cited in area',
        'CIT' => 'Citation issued',
        'CIV' => 'Civil matter',
        'CLS' => 'Closed',
        'FAL' => 'False alarm',
        'GOA' => 'Gone on arrival',
        'NAT' => 'Necessary action taken',
        'OAA' => 'Other agency assist',
        'PSR' => 'Public service report',
        'RTF' => 'Report to follow',
        'SER' => 'Service rendered',
        'UTL' => 'Unable to locate',
        'WAR' => 'Warrant arrest',
    ],
    'call_types' => [
        'AOD' => 'Assist other department',
        'AREACK' => 'Area check',
        'BUSCK' => 'Business check',
        'CIVIL' => 'Civil matter',
        'DB' => 'Dead body',
        'FD' => 'Flagged down',
        'FU' => 'Follow-up',
        'GTAREC' => 'Stolen vehicle recovery',
        'INFO' => 'Information call',
        'KTP' => 'Keep the peace',
        'LOSTP' => 'Lost property',
        'MANDOW' => 'Man down',
        'MEDAID' => 'Medical aid',
        'MISPER' => 'Missing person',
        'PATINF' => 'Patrol information',
        'PUBSER' => 'Public service',
        'REPO' => 'Repossession',
        'SECCK' => 'Security check',
        'SUBCK' => 'Subject check',
        'SUSCON' => 'Suspicious circumstances',
        'SUSPER' => 'Suspicious person',
        'SUSVEH' => 'Suspicious vehicle',
        'T' => 'Traffic detail',
        'VEHCK' => 'Vehicle check',
        'W911' => 'Wireless 911 call',
        'WARSER' => 'Warrant service',
        'WELCK' => 'Welfare check',
        'XPAT' => 'Extra patrol',
    ],
];

$candidate_specs = [];
foreach (['SBCO_CALLLOG_DESCRIPTION_FILE', 'CALLLOG_DESCRIPTION_FILE'] as $env_key) {
    $env_path = getenv($env_key);
    if (is_string($env_path) && trim($env_path) !== '') {
        $candidate_specs[] = ['path' => trim($env_path), 'section' => null];
    }
}

$candidate_specs = array_merge($candidate_specs, [
    ['path' => __DIR__ . '/calllog_descriptions.local.csv', 'section' => null],
    ['path' => __DIR__ . '/calllog_descriptions.csv', 'section' => null],
    ['path' => __DIR__ . '/calllog_desc.csv', 'section' => null],
    ['path' => __DIR__ . '/descriptions.csv', 'section' => null],
    ['path' => __DIR__ . '/descriptions/calllog.csv', 'section' => null],
    ['path' => dirname(__DIR__) . '/calllog_descriptions.csv', 'section' => null],
    ['path' => dirname(__DIR__) . '/calllog_desc.csv', 'section' => null],
    ['path' => __DIR__ . '/call_type_descriptions.csv', 'section' => 'call_types'],
    ['path' => __DIR__ . '/call_type_desc.csv', 'section' => 'call_types'],
    ['path' => __DIR__ . '/call_types.csv', 'section' => 'call_types'],
    ['path' => __DIR__ . '/disposition_descriptions.csv', 'section' => 'dispositions'],
    ['path' => __DIR__ . '/disposition_desc.csv', 'section' => 'dispositions'],
    ['path' => __DIR__ . '/dispo_descriptions.csv', 'section' => 'dispositions'],
    ['path' => __DIR__ . '/dispo_desc.csv', 'section' => 'dispositions'],
    ['path' => __DIR__ . '/dispositions.csv', 'section' => 'dispositions'],
    ['path' => __DIR__ . '/prefix_descriptions.csv', 'section' => 'prefixes'],
    ['path' => __DIR__ . '/prefix_desc.csv', 'section' => 'prefixes'],
    ['path' => __DIR__ . '/call_prefix_descriptions.csv', 'section' => 'prefixes'],
    ['path' => __DIR__ . '/call_prefix_desc.csv', 'section' => 'prefixes'],
    ['path' => __DIR__ . '/prefixes.csv', 'section' => 'prefixes'],
    ['path' => __DIR__ . '/calllog_descriptions.local.json', 'section' => null],
    ['path' => __DIR__ . '/calllog_descriptions.json', 'section' => null],
    ['path' => __DIR__ . '/descriptions/calllog.json', 'section' => null],
    ['path' => dirname(__DIR__) . '/calllog_descriptions.json', 'section' => null],
]);

$sources = [];
$descriptions = normalize_descriptions($fallback);
foreach ($candidate_specs as $spec) {
    $path = $spec['path'];
    $extension = strtolower(pathinfo($path, PATHINFO_EXTENSION));
    $loaded = $extension === 'csv'
        ? read_csv_description_file($path, $spec['section'])
        : read_json_description_file($path);
    if ($loaded === null) {
        continue;
    }
    $normalized = normalize_descriptions($loaded);
    $descriptions = [
        'prefixes' => array_replace($descriptions['prefixes'], $normalized['prefixes']),
        'dispositions' => array_replace($descriptions['dispositions'], $normalized['dispositions']),
        'call_types' => array_replace($descriptions['call_types'], $normalized['call_types']),
    ];
    $sources[] = basename($path);
}

echo json_encode([
    'ok' => true,
    'source' => $sources ? implode(',', $sources) : 'fallback',
    'prefixes' => $descriptions['prefixes'],
    'dispositions' => $descriptions['dispositions'],
    'call_types' => $descriptions['call_types'],
], JSON_UNESCAPED_SLASHES);
