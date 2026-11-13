"""Starter data: the 20 ai-conviction symbols as operator-attested instruments (no prices)."""
from decimal import Decimal
import json
from pathlib import Path
import re
import tomllib
import unittest
from portfolio_agent import starter_data
ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ['MU', 'AVGO', 'SMCI', 'TSM', 'MRVL', 'NVDA', 'AMD', 'OKLO', 'SMR', 'LEU', 'SNOW',
            'CRWV', 'ANET', 'ORCL', 'VRT', 'CCJ', 'GEV', 'BE', 'UEC', 'SKHY']
class StarterDataTests(unittest.TestCase):
    def test_symbols_match_the_watchlist_config_in_order(self):
        watchlist = json.loads((ROOT / 'config/watchlists/ai-conviction.json').read_text())
        config = [entry['symbol'] for entry in watchlist['entries']]
        self.assertEqual(config, EXPECTED)
        self.assertEqual([e['symbol'] for e in starter_data.load_starter_data()], config)
    def test_entries_are_us_stocks_with_registry_scheme_ids(self):
        for entry in starter_data.load_starter_data():
            self.assertEqual(entry['kind'], 'stock')
            self.assertEqual(entry['country'], 'US')
            self.assertEqual(entry['security_id'], 'op:' + entry['symbol'].lower())
            self.assertRegex(entry['security_id'], r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}')
            self.assertTrue(entry['source'].startswith('operator-attested:ai-conviction'))
    def test_only_skhy_is_marked_user_attested(self):
        for entry in starter_data.load_starter_data():
            self.assertEqual(entry['source'].endswith(':user-attested'), entry['symbol'] == 'SKHY')
    def test_no_price_or_value_fields_anywhere(self):
        raw = json.loads((Path(starter_data.__file__).with_name('starter_data.json')).read_text())
        text = json.dumps(raw).lower()
        for word in ('price', 'value', 'nav', 'observ'):
            self.assertNotIn(word, text)
    def test_sample_constants_are_clearly_synthetic(self):
        self.assertEqual(starter_data.SAMPLE_PORTFOLIO_NAME, 'Sample portfolio (synthetic)')
        self.assertEqual(starter_data.SAMPLE_SOURCE, 'starter-sample-synthetic')
        self.assertEqual(starter_data.SAMPLE_KEY_PREFIX, 'starter-sample-v1:')
        self.assertEqual(Decimal(starter_data.SAMPLE_QUANTITY), Decimal(100))
        self.assertIsInstance(starter_data.SAMPLE_QUANTITY, str)
    def test_data_file_is_declared_as_package_data(self):
        pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        self.assertIn('starter_data.json',
                      pyproject['tool']['setuptools']['package-data']['portfolio_agent'])
    def test_core_dependencies_stay_empty(self):
        pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        self.assertEqual(pyproject['project'].get('dependencies', []), [])
if __name__ == '__main__':
    unittest.main()
