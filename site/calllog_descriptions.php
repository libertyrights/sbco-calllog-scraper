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

function csv_combined_values(array $row, array $headers, array $names): string
{
    $values = [];
    $maxHeaderIndex = count($headers) > 0 ? max($headers) : -1;
    foreach ($names as $name) {
        $key = normalize_header($name);
        if (!isset($headers[$key])) {
            continue;
        }
        $index = $headers[$key];
        if (!array_key_exists($index, $row)) {
            continue;
        }
        if ($index === $maxHeaderIndex && count($row) > $maxHeaderIndex + 1) {
            $parts = array_map('trim', array_slice($row, $index));
            $value = trim(implode(', ', array_filter($parts, static fn($part) => $part !== '')));
        } else {
            $value = trim((string) $row[$index]);
        }
        if ($value !== '' && is_useful_description($value)) {
            $values[strtolower($value)] = $value;
        }
    }
    return implode(' - ', array_values($values));
}

function is_useful_description(string $value): bool
{
    return !in_array(strtolower(trim($value)), ['', 'unknown', 'unk', 'n/a', 'na', 'none', 'null', '?'], true);
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
    $header = fgetcsv($handle, 0, ',', '"', '\\');
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
    if (!$hasNamedColumns) {
        $headers = ['code' => 0, 'description' => 1];
        $rows[] = $header;
    }
    while (($row = fgetcsv($handle, 0, ',', '"', '\\')) !== false) {
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
        $description = csv_combined_values($row, $headers, [
            'description',
            'short_description',
            'when_used_explanation',
            'desc',
            'label',
            'meaning',
            'text',
        ]);
        if ($description === '' && count($row) >= 2) {
            $description = trim((string) $row[count($row) - 1]);
        }
        if ($code !== '' && is_useful_description($description)) {
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
        if ($code !== '' && is_useful_description($text)) {
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
        'BA' => 'Barstow Station routine patrol',
        'AJ' => 'Adelanto Jail (now known as High Desert Detention Center)',
        'BR' => 'Barstow PD',
        'CHP' => 'California Highway Patrol incident',
        'CH' => 'Chino',
        'CO' => 'Coroner',
        'CR' => 'Colorado River',
        'CS' => 'Court Services call',
        'FN' => 'Fontana',
        'GD' => 'Gang Detail',
        'GT' => 'Grand Terrace',
        'HE' => 'Hesperia',
        'HI' => 'Highland',
        'IR' => 'IRNET (Inland Regional Narcotics Enforcement Team)',
        'LL' => 'Ludlow',
        'MB' => 'Morongo Basin',
        'MT' => 'Metrolink',
        'ND' => 'Narcotics Division',
        'NE' => 'Needles',
        'OR' => 'Outreach',
        'PO' => 'Probation Ops',
        'RC' => 'Rancho Cucamonga',
        'RE' => 'Redlands',
        'SB' => 'San Bernardino City',
        'SBCFIRE' => 'San Bernardino County Fire / EMS incident',
        'SD' => 'Specialized Detectives',
        'SE' => 'Specialized Enforcement',
        'SF' => 'BNSF Police',
        'SM' => 'San Manuel',
        'SN' => 'SANCAT',
        'SP' => 'Special Patrol',
        'TO' => 'OHV Enforcement',
        'TP' => 'Twin Peaks',
        'TR' => 'Trona Substation',
        'TW' => 'Twentynine Palms',
        'UP' => 'Union Pacific Railroad Police',
        'VC' => 'Victorville City',
        'VT' => 'Victorville Transit',
        'VV' => 'Victorville County Area',
        'WE' => 'West End',
        'YU' => 'Yucaipa',
        'YV' => 'Yucca Valley',
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
    ['path' => __DIR__ . '/calltypes.csv', 'section' => 'call_types'],
    ['path' => __DIR__ . '/dispos.csv', 'section' => 'dispositions'],
    ['path' => __DIR__ . '/callprefix.csv', 'section' => 'prefixes'],
    ['path' => __DIR__ . '/calllog_descriptions.pdf.json', 'section' => null],
    ['path' => __DIR__ . '/calllog_descriptions.local.json', 'section' => null],
    ['path' => __DIR__ . '/calllog_descriptions.json', 'section' => null],
    ['path' => __DIR__ . '/descriptions/calllog.json', 'section' => null],
    ['path' => dirname(__DIR__) . '/calllog_descriptions.json', 'section' => null],
    ['path' => __DIR__ . '/calllog_descriptions.manual.json', 'section' => null],
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
