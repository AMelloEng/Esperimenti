"""
Unit tests for validatore_anagrafico.py
"""

import os
import sys
import tempfile
import unittest

# Ensure the parent directory is on sys.path so the module can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from validatore_anagrafico import (
    EXPECTED_LINE_LENGTH,
    FIELD_CODF_END,
    FIELD_CODF_START,
    FIELD_CID_END,
    FIELD_CID_START,
    FIELD_UPN_END,
    FIELD_UPN_START,
    Record,
    ValidationReport,
    _cf_checkdigit_expected,
    check_cf_duplicates,
    check_cf_format,
    check_cf_multi_cid,
    check_cid_multi_cf,
    check_upn_missing,
    parse_file,
    validate_cf,
    validate_file,
)


# ---------------------------------------------------------------------------
# Helper: build a minimal 244-char record line
# ---------------------------------------------------------------------------

def _make_line(codf: str = '', cid: str = '', upn: str = '') -> str:
    """Build a 244-character fixed-width line with the given fields."""
    line = list(' ' * EXPECTED_LINE_LENGTH)
    # CODF (0:16)
    for i, ch in enumerate(codf[:16]):
        line[FIELD_CODF_START + i] = ch
    # CID (16:24)
    for i, ch in enumerate(cid[:8]):
        line[FIELD_CID_START + i] = ch
    # UPN (104:164)
    for i, ch in enumerate(upn[:60]):
        line[FIELD_UPN_START + i] = ch
    return ''.join(line)


def _record(lineno: int, codf: str = '', cid: str = '', upn: str = '') -> Record:
    return Record(
        line_number=lineno,
        codf=codf,
        cid=cid,
        nome='',
        cognome='',
        upn=upn,
        raw=_make_line(codf, cid, upn),
    )


# ---------------------------------------------------------------------------
# 1. CF validation
# ---------------------------------------------------------------------------

class TestCFCheckDigit(unittest.TestCase):
    """Test the check-digit calculation and validate_cf helper."""

    # Real Italian CF examples (valid)
    VALID_CFS = [
        'RSSMRA85T10A562S',  # Mario Rossi – standard
        'BNCMRA80A01H501Z',  # example with valid check digit
        'MRORSS77D12H501Q',  # reversed name/surname example
    ]

    def test_checkdigit_expected_length(self):
        """_cf_checkdigit_expected must return a single uppercase letter."""
        result = _cf_checkdigit_expected('RSSMRA85T10A562')
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 1)
        self.assertTrue(result.isupper())

    def test_validate_cf_valid(self):
        """CFs with correct pattern and check-digit should return (True, True)."""
        # Generate a known-good CF programmatically
        prefix = 'RSSMRA85T10A562'
        expected_cd = _cf_checkdigit_expected(prefix)
        cf = prefix + expected_cd
        pattern_ok, cd_ok = validate_cf(cf)
        self.assertTrue(pattern_ok, f"Pattern should be OK for {cf}")
        self.assertTrue(cd_ok, f"Check-digit should be OK for {cf}")

    def test_validate_cf_bad_pattern(self):
        """CFs that don't match the pattern should return (False, False)."""
        bad_cfs = [
            'TOOSHORT',
            '1234567890123456',      # starts with digits
            'RSSMRA85T10A562SS',     # too long
            'rssmra85T10A562S',      # lowercase
            '',
        ]
        for cf in bad_cfs:
            with self.subTest(cf=cf):
                pattern_ok, cd_ok = validate_cf(cf)
                self.assertFalse(pattern_ok, f"Should fail pattern: {cf!r}")
                self.assertFalse(cd_ok, f"Should fail checkdigit too: {cf!r}")

    def test_validate_cf_bad_checkdigit(self):
        """CF with valid pattern but wrong last character should return (True, False)."""
        prefix = 'RSSMRA85T10A562'
        correct_cd = _cf_checkdigit_expected(prefix)
        # Replace with a different letter
        wrong_cd = 'A' if correct_cd != 'A' else 'B'
        cf = prefix + wrong_cd
        pattern_ok, cd_ok = validate_cf(cf)
        self.assertTrue(pattern_ok, f"Pattern should pass for {cf}")
        self.assertFalse(cd_ok, f"Check-digit should fail for {cf}")

    def test_validate_cf_omocodia(self):
        """CFs with omocodia (digit substituted with letter) should be accepted by pattern."""
        # Replace digit chars in positions 7-8 with omocodia letters (L=0)
        prefix = 'RSSMRALL' + 'T' + '10A562'   # LL replaces 85 → L=8 per omocodia? Let's just
        # test that the pattern accepts the LMNPQRSTUV chars in those positions
        prefix2 = 'RSSMRA' + 'LM' + 'T' + 'LM' + 'A' + 'LMN' + 'X'
        # ^^ just check it matches pattern
        import re
        from validatore_anagrafico import _CF_PATTERN
        self.assertIsNotNone(_CF_PATTERN.match(prefix2))

    def test_month_codes_all_accepted(self):
        """All 12 month codes (ABCDEHLMPRST) should be accepted in position 9."""
        from validatore_anagrafico import _CF_PATTERN
        month_codes = 'ABCDEHLMPRST'
        for month in month_codes:
            cf = f'RSSMRA85{month}10A562X'
            with self.subTest(month=month):
                self.assertIsNotNone(
                    _CF_PATTERN.match(cf),
                    f"Month code {month!r} should be accepted",
                )

    def test_invalid_month_codes_rejected(self):
        """Characters not in ABCDEHLMPRST must be rejected in position 9."""
        from validatore_anagrafico import _CF_PATTERN
        invalid_months = 'FGIJKNOQUVWXYZ'
        for ch in invalid_months:
            cf = f'RSSMRA85{ch}10A562X'
            with self.subTest(ch=ch):
                self.assertIsNone(
                    _CF_PATTERN.match(cf),
                    f"Invalid month code {ch!r} should be rejected",
                )


# ---------------------------------------------------------------------------
# 2. Check CF duplicates
# ---------------------------------------------------------------------------

class TestCheckCFDuplicates(unittest.TestCase):

    def test_no_duplicates(self):
        records = [
            _record(1, 'RSSMRA85T10A562S', '00000001'),
            _record(2, 'BNCMRA80A01H501Z', '00000002'),
        ]
        result = check_cf_duplicates(records)
        self.assertEqual(result, [])

    def test_one_duplicate(self):
        cf = 'RSSMRA85T10A562S'
        records = [
            _record(1, cf, '00000001'),
            _record(2, cf, '00000001'),
        ]
        result = check_cf_duplicates(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['CODF'], cf)
        self.assertEqual(result[0]['n_occorrenze'], 2)
        self.assertIn(1, result[0]['linee'])
        self.assertIn(2, result[0]['linee'])

    def test_empty_codf_ignored(self):
        records = [
            _record(1, '', '00000001'),
            _record(2, '', '00000002'),
        ]
        result = check_cf_duplicates(records)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# 3. Same CF → multiple CIDs
# ---------------------------------------------------------------------------

class TestCheckCFMultiCID(unittest.TestCase):

    def test_no_conflict(self):
        cf = 'RSSMRA85T10A562S'
        records = [
            _record(1, cf, '00000001'),
            _record(2, cf, '00000001'),  # same CID, not a conflict
        ]
        result = check_cf_multi_cid(records)
        self.assertEqual(result, [])

    def test_conflict(self):
        cf = 'RSSMRA85T10A562S'
        records = [
            _record(1, cf, '00000001'),
            _record(2, cf, '00000002'),  # different CID → conflict
        ]
        result = check_cf_multi_cid(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['CODF'], cf)
        self.assertIn('00000001', result[0]['CID_diversi'])
        self.assertIn('00000002', result[0]['CID_diversi'])


# ---------------------------------------------------------------------------
# 4. Same CID → multiple CFs
# ---------------------------------------------------------------------------

class TestCheckCIDMultiCF(unittest.TestCase):

    def test_no_conflict(self):
        cid = '00000001'
        records = [
            _record(1, 'RSSMRA85T10A562S', cid),
            _record(2, 'RSSMRA85T10A562S', cid),  # same CF, not a conflict
        ]
        result = check_cid_multi_cf(records)
        self.assertEqual(result, [])

    def test_conflict(self):
        cid = '00000001'
        records = [
            _record(1, 'RSSMRA85T10A562S', cid),
            _record(2, 'BNCMRA80A01H501Z', cid),  # different CF → conflict
        ]
        result = check_cid_multi_cf(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['CID'], cid)
        self.assertIn('RSSMRA85T10A562S', result[0]['CODF_diversi'])
        self.assertIn('BNCMRA80A01H501Z', result[0]['CODF_diversi'])


# ---------------------------------------------------------------------------
# 5. UPN missing / incomplete
# ---------------------------------------------------------------------------

class TestCheckUPNMissing(unittest.TestCase):

    def test_valid_upn(self):
        records = [_record(1, upn='mario.rossi@example.com')]
        result = check_upn_missing(records)
        self.assertEqual(result, [])

    def test_empty_upn(self):
        records = [_record(1, upn='')]
        result = check_upn_missing(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['anomalia'], 'UPN assente')

    def test_upn_without_at(self):
        records = [_record(1, upn='mario.rossi.example.com')]
        result = check_upn_missing(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['anomalia'], 'UPN senza @')

    def test_whitespace_only_upn(self):
        """A UPN that is all spaces strips to '' and is treated as absent."""
        records = [_record(1, upn='   ')]
        # The record's upn field is stripped by parse_file; simulate manually
        rec = Record(1, '', '', '', '', '', '')
        result = check_upn_missing([rec])
        self.assertEqual(len(result), 1)


# ---------------------------------------------------------------------------
# 6. File parsing
# ---------------------------------------------------------------------------

class TestParseFile(unittest.TestCase):

    def _write_temp(self, lines, encoding='utf-8', newline='\n'):
        tf = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding=encoding, newline=newline
        )
        for line in lines:
            tf.write(line + newline)
        tf.flush()
        tf.close()
        return tf.name

    def test_correct_length(self):
        line = _make_line('RSSMRA85T10A562S', '00000001', 'mario@example.com')
        path = self._write_temp([line])
        try:
            records, anomalies = parse_file(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(anomalies, [])
            self.assertEqual(records[0].codf, 'RSSMRA85T10A562S')
            self.assertEqual(records[0].cid, '00000001')
            self.assertIn('@', records[0].upn)
        finally:
            os.unlink(path)

    def test_short_line_reported(self):
        short_line = 'A' * 100  # only 100 chars
        path = self._write_temp([short_line])
        try:
            records, anomalies = parse_file(path)
            self.assertEqual(len(anomalies), 1)
            self.assertEqual(anomalies[0]['lunghezza_effettiva'], 100)
            self.assertEqual(anomalies[0]['lunghezza_attesa'], EXPECTED_LINE_LENGTH)
        finally:
            os.unlink(path)

    def test_long_line_reported(self):
        long_line = 'B' * 300
        path = self._write_temp([long_line])
        try:
            records, anomalies = parse_file(path)
            self.assertEqual(len(anomalies), 1)
            self.assertEqual(anomalies[0]['lunghezza_effettiva'], 300)
        finally:
            os.unlink(path)

    def test_multiple_records(self):
        lines = [
            _make_line('RSSMRA85T10A562S', '00000001', 'mario@example.com'),
            _make_line('BNCMRA80A01H501Z', '00000002', 'bianchi@example.com'),
        ]
        path = self._write_temp(lines)
        try:
            records, anomalies = parse_file(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(anomalies, [])
            self.assertEqual(records[0].line_number, 1)
            self.assertEqual(records[1].line_number, 2)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 7. Integration — validate_file end-to-end
# ---------------------------------------------------------------------------

class TestValidateFileIntegration(unittest.TestCase):

    def _write_temp(self, lines, encoding='utf-8'):
        tf = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding=encoding
        )
        for line in lines:
            tf.write(line + '\n')
        tf.flush()
        tf.close()
        return tf.name

    def test_clean_file_no_anomalies(self):
        prefix = 'RSSMRA85T10A562'
        from validatore_anagrafico import _cf_checkdigit_expected
        cf1 = prefix + _cf_checkdigit_expected(prefix)

        prefix2 = 'BNCMRA80A01H501'
        cf2 = prefix2 + _cf_checkdigit_expected(prefix2)

        lines = [
            _make_line(cf1, '00000001', 'mario@example.com'),
            _make_line(cf2, '00000002', 'bianca@example.com'),
        ]
        path = self._write_temp(lines)
        try:
            report = validate_file(path, no_export=True)
            self.assertEqual(report.cf_pattern_errors, [])
            self.assertEqual(report.cf_checkdigit_errors, [])
            self.assertEqual(report.cf_duplicates, [])
            self.assertEqual(report.cf_multi_cid, [])
            self.assertEqual(report.cid_multi_cf, [])
            self.assertEqual(report.upn_missing, [])
            self.assertEqual(report.line_length_anomalies, [])
            self.assertEqual(report.total_records, 2)
        finally:
            os.unlink(path)

    def test_file_with_duplicate_cf(self):
        prefix = 'RSSMRA85T10A562'
        from validatore_anagrafico import _cf_checkdigit_expected
        cf = prefix + _cf_checkdigit_expected(prefix)
        lines = [
            _make_line(cf, '00000001', 'mario@example.com'),
            _make_line(cf, '00000001', 'mario2@example.com'),
        ]
        path = self._write_temp(lines)
        try:
            report = validate_file(path, no_export=True)
            self.assertEqual(len(report.cf_duplicates), 1)
        finally:
            os.unlink(path)

    def test_file_with_missing_upn(self):
        prefix = 'RSSMRA85T10A562'
        from validatore_anagrafico import _cf_checkdigit_expected
        cf = prefix + _cf_checkdigit_expected(prefix)
        lines = [
            _make_line(cf, '00000001', ''),  # no UPN
        ]
        path = self._write_temp(lines)
        try:
            report = validate_file(path, no_export=True)
            self.assertEqual(len(report.upn_missing), 1)
        finally:
            os.unlink(path)

    def test_csv_export(self):
        prefix = 'RSSMRA85T10A562'
        from validatore_anagrafico import _cf_checkdigit_expected
        cf = prefix + _cf_checkdigit_expected(prefix)
        lines = [_make_line(cf, '00000001', '')]  # missing UPN → generates one CSV
        path = self._write_temp(lines)
        with tempfile.TemporaryDirectory() as export_dir:
            try:
                validate_file(path, export_dir=export_dir)
                exported = os.listdir(export_dir)
                self.assertTrue(
                    any('upn_mancante' in f for f in exported),
                    f"Expected upn_mancante CSV, got: {exported}",
                )
            finally:
                os.unlink(path)


if __name__ == '__main__':
    unittest.main()
