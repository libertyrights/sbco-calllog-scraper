<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function read_description_file(string $path): ?array
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

$candidate_paths = [];
foreach (['SBCO_CALLLOG_DESCRIPTION_FILE', 'CALLLOG_DESCRIPTION_FILE'] as $env_key) {
    $env_path = getenv($env_key);
    if (is_string($env_path) && trim($env_path) !== '') {
        $candidate_paths[] = trim($env_path);
    }
}

$candidate_paths = array_merge($candidate_paths, [
    __DIR__ . '/calllog_descriptions.local.json',
    __DIR__ . '/calllog_descriptions.json',
    __DIR__ . '/descriptions/calllog.json',
    dirname(__DIR__) . '/calllog_descriptions.json',
]);

$source = 'fallback';
$descriptions = normalize_descriptions($fallback);
foreach ($candidate_paths as $path) {
    $loaded = read_description_file($path);
    if ($loaded === null) {
        continue;
    }
    $normalized = normalize_descriptions($loaded);
    $descriptions = [
        'prefixes' => array_replace($descriptions['prefixes'], $normalized['prefixes']),
        'dispositions' => array_replace($descriptions['dispositions'], $normalized['dispositions']),
        'call_types' => array_replace($descriptions['call_types'], $normalized['call_types']),
    ];
    $source = basename($path);
    break;
}

echo json_encode([
    'ok' => true,
    'source' => $source,
    'prefixes' => $descriptions['prefixes'],
    'dispositions' => $descriptions['dispositions'],
    'call_types' => $descriptions['call_types'],
], JSON_UNESCAPED_SLASHES);
