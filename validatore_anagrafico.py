#!/usr/bin/env python3
"""
Agente di Validazione — File Anagrafico (buoni pasto elettronici)

Struttura SAP ZTMIT105 — tracciato record a larghezza fissa (244 caratteri/riga).

Controlli eseguiti:
  1. CF formalmente corretti (pattern + check-digit, omocodia inclusa)
  2. CF duplicati
  3. Stesso CF → CID diversi
  4. Stesso CID → CF diversi
  5. UPN mancante o incompleta (posizioni 105-164, 0-based 104:164)

Utilizzo:
  python validatore_anagrafico.py <file.txt> [--export-dir <directory>]

Argomenti opzionali:
  --export-dir  Cartella in cui salvare i CSV di anomalie (default: directory del file)
  --encoding    Encoding del file (default: utf-8)
  --no-export   Non esportare CSV, solo report a terminale
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from typing import List, NamedTuple, Dict, Set, Tuple

# ---------------------------------------------------------------------------
# Costanti tracciato record (0-based, Python slice notation)
# ---------------------------------------------------------------------------

EXPECTED_LINE_LENGTH = 244

FIELD_CODF_START = 0
FIELD_CODF_END = 16   # CF (Codice Fiscale) — 16 caratteri

FIELD_CID_START = 16
FIELD_CID_END = 24    # CID / PERNR — 8 caratteri

FIELD_NOME_START = 24
FIELD_NOME_END = 64   # Nome — 40 caratteri (empirici)

FIELD_COGNOME_START = 64
FIELD_COGNOME_END = 104  # Cognome — 40 caratteri (empirici)

FIELD_UPN_START = 104
FIELD_UPN_END = 164   # UPN / e-mail — 60 caratteri

# ---------------------------------------------------------------------------
# Tabelle ufficiali per il calcolo del carattere di controllo del CF
# ---------------------------------------------------------------------------

# Valori per caratteri in posizione DISPARI (1, 3, 5, … 1-based, cioè indici 0, 2, 4, …)
_CF_ODD: Dict[str, int] = {
    '0': 1,  '1': 0,  '2': 5,  '3': 7,  '4': 9,
    '5': 13, '6': 15, '7': 17, '8': 19, '9': 21,
    'A': 1,  'B': 0,  'C': 5,  'D': 7,  'E': 9,
    'F': 13, 'G': 15, 'H': 17, 'I': 19, 'J': 21,
    'K': 2,  'L': 4,  'M': 18, 'N': 20, 'O': 11,
    'P': 3,  'Q': 6,  'R': 8,  'S': 12, 'T': 14,
    'U': 16, 'V': 10, 'W': 22, 'X': 25, 'Y': 24,
    'Z': 23,
}

# Valori per caratteri in posizione PARI (2, 4, 6, … 1-based, cioè indici 1, 3, 5, …)
_CF_EVEN: Dict[str, int] = {
    '0': 0,  '1': 1,  '2': 2,  '3': 3,  '4': 4,
    '5': 5,  '6': 6,  '7': 7,  '8': 8,  '9': 9,
    'A': 0,  'B': 1,  'C': 2,  'D': 3,  'E': 4,
    'F': 5,  'G': 6,  'H': 7,  'I': 8,  'J': 9,
    'K': 10, 'L': 11, 'M': 12, 'N': 13, 'O': 14,
    'P': 15, 'Q': 16, 'R': 17, 'S': 18, 'T': 19,
    'U': 20, 'V': 21, 'W': 22, 'X': 23, 'Y': 24,
    'Z': 25,
}

_CF_REMAINDER_TO_CHAR: Dict[int, str] = {
    0: 'A', 1: 'B', 2: 'C', 3: 'D', 4: 'E', 5: 'F', 6: 'G',
    7: 'H', 8: 'I', 9: 'J', 10: 'K', 11: 'L', 12: 'M', 13: 'N',
    14: 'O', 15: 'P', 16: 'Q', 17: 'R', 18: 'S', 19: 'T', 20: 'U',
    21: 'V', 22: 'W', 23: 'X', 24: 'Y', 25: 'Z',
}

# Pattern CF tollerante all'omocodia
# Pos 7-8  (idx 6-7) : [0-9LMNPQRSTUV]
# Pos 9    (idx 8)   : lettera mese [ABCDEHLMPRST]
# Pos 10-11(idx 9-10): [0-9LMNPQRSTUV]
# Pos 12   (idx 11)  : lettera comune [A-Z]
# Pos 13-15(idx 12-14): [0-9LMNPQRSTUV]
# Pos 16   (idx 15)  : carattere di controllo [A-Z]
_CF_PATTERN = re.compile(
    r'^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}'
    r'[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$'
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Record(NamedTuple):
    line_number: int
    codf: str
    cid: str
    nome: str
    cognome: str
    upn: str
    raw: str


class ValidationReport(NamedTuple):
    cf_pattern_errors: List[Dict]
    cf_checkdigit_errors: List[Dict]
    cf_duplicates: List[Dict]
    cf_multi_cid: List[Dict]
    cid_multi_cf: List[Dict]
    upn_missing: List[Dict]
    line_length_anomalies: List[Dict]
    total_records: int


# ---------------------------------------------------------------------------
# CF validation helpers
# ---------------------------------------------------------------------------

def _cf_checkdigit_expected(cf15: str) -> str:
    """Calcola il carattere di controllo atteso per i primi 15 caratteri di un CF."""
    total = 0
    for i, ch in enumerate(cf15):
        if (i + 1) % 2 == 1:   # posizione dispari (1-based)
            total += _CF_ODD.get(ch, 0)
        else:                    # posizione pari
            total += _CF_EVEN.get(ch, 0)
    return _CF_REMAINDER_TO_CHAR[total % 26]


def validate_cf(cf: str) -> Tuple[bool, bool]:
    """
    Valida un Codice Fiscale così com'è estratto dal file (senza normalizzazioni).

    Returns:
        (pattern_ok, checkdigit_ok)
        Se pattern_ok è False, checkdigit_ok è False per definizione.

    Note: il CF non viene convertito in maiuscolo prima della verifica.
    Se il file contiene caratteri minuscoli, il controllo li rifiuterà
    correttamente come anomalia di qualità dati.
    """
    cf = cf.strip()
    if not _CF_PATTERN.match(cf):
        return False, False
    expected = _cf_checkdigit_expected(cf[:15])
    return True, (cf[15] == expected)


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------

def parse_file(filepath: str, encoding: str = 'utf-8') -> Tuple[List[Record], List[Dict]]:
    """
    Legge il file e restituisce (records, anomalie_lunghezza).
    """
    records: List[Record] = []
    length_anomalies: List[Dict] = []

    with open(filepath, 'r', encoding=encoding, errors='replace') as fh:
        for lineno, raw in enumerate(fh, start=1):
            # Rimuovi solo il newline finale (non altri spazi significativi)
            line = raw.rstrip('\r\n')
            length = len(line)

            if length != EXPECTED_LINE_LENGTH:
                length_anomalies.append({
                    'linea': lineno,
                    'lunghezza_effettiva': length,
                    'lunghezza_attesa': EXPECTED_LINE_LENGTH,
                })
                # Pad or truncate to avoid index errors on other checks
                if length < EXPECTED_LINE_LENGTH:
                    line = line.ljust(EXPECTED_LINE_LENGTH)
                else:
                    line = line[:EXPECTED_LINE_LENGTH]

            codf = line[FIELD_CODF_START:FIELD_CODF_END].strip()
            cid = line[FIELD_CID_START:FIELD_CID_END].strip()
            nome = line[FIELD_NOME_START:FIELD_NOME_END].strip()
            cognome = line[FIELD_COGNOME_START:FIELD_COGNOME_END].strip()
            upn = line[FIELD_UPN_START:FIELD_UPN_END].strip()

            records.append(Record(
                line_number=lineno,
                codf=codf,
                cid=cid,
                nome=nome,
                cognome=cognome,
                upn=upn,
                raw=raw.rstrip('\r\n'),
            ))

    return records, length_anomalies


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def check_cf_format(records: List[Record]) -> Tuple[List[Dict], List[Dict]]:
    """
    Controllo 1: CF formalmente corretto (pattern + check-digit).

    Returns:
        (cf_pattern_errors, cf_checkdigit_errors)
    """
    pattern_errors: List[Dict] = []
    checkdigit_errors: List[Dict] = []

    for rec in records:
        pattern_ok, checkdigit_ok = validate_cf(rec.codf)
        if not pattern_ok:
            pattern_errors.append({
                'linea': rec.line_number,
                'CODF': rec.codf,
                'CID': rec.cid,
                'anomalia': 'Pattern CF non valido',
            })
        elif not checkdigit_ok:
            checkdigit_errors.append({
                'linea': rec.line_number,
                'CODF': rec.codf,
                'CID': rec.cid,
                'anomalia': 'Check-digit CF errato',
            })

    return pattern_errors, checkdigit_errors


def check_cf_duplicates(records: List[Record]) -> List[Dict]:
    """
    Controllo 2: CF duplicati (stesso CF in più righe).
    """
    cf_lines: Dict[str, List[Record]] = defaultdict(list)
    for rec in records:
        if rec.codf:
            cf_lines[rec.codf].append(rec)

    duplicates: List[Dict] = []
    for cf, recs in cf_lines.items():
        if len(recs) > 1:
            duplicates.append({
                'CODF': cf,
                'n_occorrenze': len(recs),
                'linee': [r.line_number for r in recs],
                'CID_values': list({r.cid for r in recs}),
            })

    duplicates.sort(key=lambda x: x['n_occorrenze'], reverse=True)
    return duplicates


def check_cf_multi_cid(records: List[Record]) -> List[Dict]:
    """
    Controllo 3: Stesso CF → CID diversi.
    """
    cf_cids: Dict[str, Set[str]] = defaultdict(set)
    cf_lines: Dict[str, List[int]] = defaultdict(list)

    for rec in records:
        if rec.codf:
            cf_cids[rec.codf].add(rec.cid)
            cf_lines[rec.codf].append(rec.line_number)

    result: List[Dict] = []
    for cf, cids in cf_cids.items():
        if len(cids) > 1:
            result.append({
                'CODF': cf,
                'CID_diversi': sorted(cids),
                'linee': cf_lines[cf],
            })

    return result


def check_cid_multi_cf(records: List[Record]) -> List[Dict]:
    """
    Controllo 4: Stesso CID → CF diversi.
    """
    cid_cfs: Dict[str, Set[str]] = defaultdict(set)
    cid_lines: Dict[str, List[int]] = defaultdict(list)

    for rec in records:
        if rec.cid:
            cid_cfs[rec.cid].add(rec.codf)
            cid_lines[rec.cid].append(rec.line_number)

    result: List[Dict] = []
    for cid, cfs in cid_cfs.items():
        if len(cfs) > 1:
            result.append({
                'CID': cid,
                'CODF_diversi': sorted(cfs),
                'linee': cid_lines[cid],
            })

    return result


def check_upn_missing(records: List[Record]) -> List[Dict]:
    """
    Controllo 5: UPN mancante o incompleta.
    upn_mancante = (upn == '') or ('@' not in upn)
    """
    result: List[Dict] = []
    for rec in records:
        upn = rec.upn
        if upn == '' or '@' not in upn:
            result.append({
                'linea': rec.line_number,
                'CODF': rec.codf,
                'CID': rec.cid,
                'UPN': upn if upn else '(vuoto)',
                'anomalia': 'UPN assente' if upn == '' else 'UPN senza @',
            })
    return result


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def _table_header(title: str, count: int) -> str:
    sep = '─' * 70
    return f"\n{sep}\n  {title}\n  Totale anomalie: {count}\n{sep}"


def _preview(items: List[Dict], n: int = 20) -> str:
    if not items:
        return '  Nessuna anomalia rilevata.\n'
    shown = items[:n]
    lines = []
    for item in shown:
        lines.append('  ' + '  |  '.join(f'{k}: {v}' for k, v in item.items()))
    if len(items) > n:
        lines.append(f'  … e altri {len(items) - n} casi (vedi CSV esportato)')
    return '\n'.join(lines) + '\n'


def print_report(report: ValidationReport, filepath: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"  REPORT DI QUALITÀ DATI — {os.path.basename(filepath)}")
    print(f"  Righe totali analizzate: {report.total_records}")
    print(f"{'═' * 70}")

    if report.line_length_anomalies:
        print(_table_header(
            'ANOMALIE LUNGHEZZA RIGA (attesa: 244 caratteri)',
            len(report.line_length_anomalies),
        ))
        print(_preview(report.line_length_anomalies))

    print(_table_header(
        'CONTROLLO 1a — CF con PATTERN non valido',
        len(report.cf_pattern_errors),
    ))
    print(_preview(report.cf_pattern_errors))

    print(_table_header(
        'CONTROLLO 1b — CF con check-digit errato (pattern OK)',
        len(report.cf_checkdigit_errors),
    ))
    print(_preview(report.cf_checkdigit_errors))

    print(_table_header(
        'CONTROLLO 2 — CF duplicati',
        len(report.cf_duplicates),
    ))
    print(_preview(report.cf_duplicates))

    print(_table_header(
        'CONTROLLO 3 — Stesso CF → CID diversi',
        len(report.cf_multi_cid),
    ))
    print(_preview(report.cf_multi_cid))

    print(_table_header(
        'CONTROLLO 4 — Stesso CID → CF diversi',
        len(report.cid_multi_cf),
    ))
    print(_preview(report.cid_multi_cf))

    print(_table_header(
        'CONTROLLO 5 — UPN mancante o incompleta',
        len(report.upn_missing),
    ))
    print(_preview(report.upn_missing))

    # Summary
    total_anomalies = (
        len(report.line_length_anomalies)
        + len(report.cf_pattern_errors)
        + len(report.cf_checkdigit_errors)
        + len(report.cf_duplicates)
        + len(report.cf_multi_cid)
        + len(report.cid_multi_cf)
        + len(report.upn_missing)
    )
    print(f"\n{'═' * 70}")
    print(f"  RIEPILOGO FINALE")
    print(f"{'═' * 70}")
    print(f"  Righe totali:                 {report.total_records:>10}")
    print(f"  Anomalie lunghezza riga:       {len(report.line_length_anomalies):>10}")
    print(f"  CF pattern errato:             {len(report.cf_pattern_errors):>10}")
    print(f"  CF check-digit errato:         {len(report.cf_checkdigit_errors):>10}")
    print(f"  CF duplicati:                  {len(report.cf_duplicates):>10}")
    print(f"  CF con CID diversi:            {len(report.cf_multi_cid):>10}")
    print(f"  CID con CF diversi:            {len(report.cid_multi_cf):>10}")
    print(f"  UPN mancante/incompleta:       {len(report.upn_missing):>10}")
    print(f"  ─────────────────────────────────────────")
    print(f"  TOTALE ANOMALIE:               {total_anomalies:>10}")
    print(f"{'═' * 70}\n")


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_csv(items: List[Dict], filepath: str) -> None:
    if not items:
        return
    os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
    fieldnames = list(items[0].keys())
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in items:
            # Convert lists to semicolon-separated strings for CSV readability
            writer.writerow({
                k: ';'.join(str(i) for i in v) if isinstance(v, list) else v
                for k, v in row.items()
            })
    print(f"  → Esportato: {filepath}")


def export_all_csvs(report: ValidationReport, base_dir: str, base_name: str) -> None:
    def path(suffix: str) -> str:
        return os.path.join(base_dir, f'{base_name}_{suffix}.csv')

    if report.line_length_anomalies:
        export_csv(report.line_length_anomalies, path('anomalie_lunghezza'))
    if report.cf_pattern_errors:
        export_csv(report.cf_pattern_errors, path('cf_pattern_errato'))
    if report.cf_checkdigit_errors:
        export_csv(report.cf_checkdigit_errors, path('cf_checkdigit_errato'))
    if report.cf_duplicates:
        export_csv(report.cf_duplicates, path('cf_duplicati'))
    if report.cf_multi_cid:
        export_csv(report.cf_multi_cid, path('cf_multi_cid'))
    if report.cid_multi_cf:
        export_csv(report.cid_multi_cf, path('cid_multi_cf'))
    if report.upn_missing:
        export_csv(report.upn_missing, path('upn_mancante'))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_file(
    filepath: str,
    encoding: str = 'utf-8',
    export_dir: str | None = None,
    no_export: bool = False,
) -> ValidationReport:
    """
    Esegue tutti i controlli sul file e restituisce il ValidationReport.
    Stampa il report a terminale ed esporta i CSV (se no_export=False).
    """
    print(f"\nCaricamento file: {filepath}")
    records, length_anomalies = parse_file(filepath, encoding=encoding)
    print(f"Righe lette: {len(records)}")

    cf_pattern_errors, cf_checkdigit_errors = check_cf_format(records)
    cf_duplicates = check_cf_duplicates(records)
    cf_multi_cid = check_cf_multi_cid(records)
    cid_multi_cf = check_cid_multi_cf(records)
    upn_missing = check_upn_missing(records)

    report = ValidationReport(
        cf_pattern_errors=cf_pattern_errors,
        cf_checkdigit_errors=cf_checkdigit_errors,
        cf_duplicates=cf_duplicates,
        cf_multi_cid=cf_multi_cid,
        cid_multi_cf=cid_multi_cf,
        upn_missing=upn_missing,
        line_length_anomalies=length_anomalies,
        total_records=len(records),
    )

    print_report(report, filepath)

    if not no_export:
        base_dir = export_dir or os.path.dirname(os.path.abspath(filepath))
        base_name = os.path.splitext(os.path.basename(filepath))[0]
        print(f"\nEsportazione CSV in: {base_dir}")
        export_all_csvs(report, base_dir, base_name)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Validatore file anagrafico buoni pasto elettronici (SAP ZTMIT105)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('file', help='Percorso del file da analizzare (.txt)')
    parser.add_argument(
        '--export-dir',
        metavar='DIR',
        help='Cartella di destinazione per i CSV di anomalie',
    )
    parser.add_argument(
        '--encoding',
        default='utf-8',
        help='Encoding del file di input (default: utf-8)',
    )
    parser.add_argument(
        '--no-export',
        action='store_true',
        help='Non esportare CSV, solo report a terminale',
    )

    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"Errore: file non trovato: {args.file}", file=sys.stderr)
        sys.exit(1)

    validate_file(
        filepath=args.file,
        encoding=args.encoding,
        export_dir=args.export_dir,
        no_export=args.no_export,
    )


if __name__ == '__main__':
    main()
