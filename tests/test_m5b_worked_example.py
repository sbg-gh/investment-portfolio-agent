"""Canonical worked example: dated-NAV for mutual funds instead of the intraday quote."""
import unittest

from tests.m5b_fixtures import main_run


class WorkedExampleTests(unittest.TestCase):
    def setUp(self):
        self.result = main_run()
        self.rows = {n: [r for r in v['rows'] if r['role'] == 'target']
                     for n, v in self.result['variants'].items()}

    def test_approved_procedure_changes_the_tool_sequence_for_fund_cases(self):
        self.assertTrue(all(r['sequence'] == ['dated_nav'] for r in self.rows['approved_procedure']))
        for comparator in ('no_memory', 'retrieval_only'):
            self.assertTrue(all(r['sequence'] == ['intraday_quote'] for r in self.rows[comparator]))

    def test_stale_price_misuse_rate_is_reported_per_variant(self):
        rates = self.result['worked_example']['stale_price_misuse_rate']
        self.assertEqual(set(rates), {'no_memory', 'retrieval_only', 'approved_procedure'})
        for name, rate in rates.items():
            rows = self.rows[name]
            self.assertEqual(rate, sum(r['stale_price_misuse'] for r in rows) / len(rows))
        self.assertEqual(rates['approved_procedure'], 0)
        # The intraday feed lags by i % 4 days, so exactly 3 of 4 target cases misuse a stale price.
        self.assertEqual(rates['no_memory'], 0.75)
        self.assertEqual(rates['retrieval_only'], 0.75)

    def test_retrieval_only_is_not_counted_as_adaptation(self):
        example = self.result['worked_example']
        self.assertEqual(example['sequence_changed_vs_no_memory']['retrieval_only'], False)
        self.assertEqual(example['credited_as_adaptation']['retrieval_only'], False)
        self.assertEqual(example['credited_as_adaptation']['no_memory'], False)
        self.assertEqual(example['credited_as_adaptation']['approved_procedure'],
                         example['sequence_changed_vs_no_memory']['approved_procedure'])

    def test_retrieval_only_variant_really_received_the_candidate_text(self):
        # Fairness: retrieval-only got the prior explanation and the unreviewed candidate.
        self.assertTrue(all(r['memories_retrieved'] > 0 for r in self.rows['retrieval_only']))
        self.assertTrue(all(r['memories_retrieved'] == 0 for r in self.rows['approved_procedure']))
        self.assertTrue(all(r['procedures_injected'] > 0 for r in self.rows['approved_procedure']))
        self.assertTrue(all(r['procedures_injected'] == 0 for r in self.rows['retrieval_only']))

    def test_control_families_are_unchanged_for_the_approved_variant(self):
        approved = {r['case_id']: r for r in self.result['variants']['approved_procedure']['rows']}
        baseline = {r['case_id']: r for r in self.result['variants']['no_memory']['rows']}
        for cid, row in approved.items():
            if row['role'] == 'control':
                self.assertEqual(row['sequence'], baseline[cid]['sequence'])
        equity = [r for r in approved.values() if r['family'] == 'equity_quote']
        self.assertTrue(all(r['sequence'] == ['intraday_quote'] for r in equity))
        holdings = [r for r in approved.values() if r['family'] == 'holdings_list']
        self.assertTrue(all(r['sequence'] == ['list_holdings'] for r in holdings))

    def test_assumption_is_stated_honestly_in_the_report(self):
        self.assertIn('plumbing', self.result['assumption'])
        self.assertIn('not', self.result['assumption'])


if __name__ == '__main__':
    unittest.main()
