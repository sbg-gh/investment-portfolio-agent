"""M5b held-out dataset generator: derived ONLY from m5b_prereg.json (no hand-tuned constants)."""
import copy
from datetime import datetime, timedelta
import json
import unittest

from portfolio_agent.leakage import make_canary
from portfolio_agent.m5b_dataset import build_dataset
from portfolio_agent.m5b_stats import load_prereg


def parse(ts):
    return datetime.fromisoformat(ts)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.prereg, _ = load_prereg()
        self.dataset = build_dataset()
        self.cases = self.dataset['cases']

    def families(self):
        out = {}
        for case in self.cases:
            out.setdefault(case['family'], []).append(case)
        return out

    def test_counts_equal_prereg(self):
        counts = {family: len(cases) for family, cases in self.families().items()}
        self.assertEqual(counts, self.prereg['family_counts'])
        self.assertEqual(self.dataset['n_target'], self.prereg['n_target'])
        self.assertEqual(
            sum(1 for c in self.cases if c['role'] == 'target'), self.prereg['n_target'])
        training = self.dataset['training']['runs']
        mix = {(r['task_type'], tuple(r['tool_sequence']), r['outcome']): 0 for r in training}
        for run in training:
            mix[(run['task_type'], tuple(run['tool_sequence']), run['outcome'])] += 1
        self.assertEqual(mix, {(m['task_type'], tuple(m['tool_sequence']), m['outcome']): m['count']
                               for m in self.prereg['training']['run_mix']})

    def test_counts_follow_a_modified_prereg_not_constants(self):
        small = copy.deepcopy(self.prereg)
        small['family_counts'].update(fund_value_question=4, fund_nav_report=4)
        small['n_target'] = 8
        data = build_dataset(small)
        self.assertEqual(data['n_target'], 8)
        self.assertEqual(len(data['cases']), sum(small['family_counts'].values()))

    def test_held_out_strictly_after_training_and_training_in_range(self):
        training_dates = [parse(r['observation_timestamps'][0]) for r in self.dataset['training']['runs']]
        start = datetime.fromisoformat(self.prereg['training']['as_of_start'] + 'T21:00:00+00:00')
        end = datetime.fromisoformat(self.prereg['training']['as_of_end'] + 'T21:00:00+00:00')
        self.assertTrue(all(start <= d <= end for d in training_dates))
        first_held_out = datetime.fromisoformat(
            self.prereg['held_out']['as_of_start'] + 'T21:00:00+00:00')
        for case in self.cases:
            self.assertGreaterEqual(parse(case['as_of']), first_held_out)
            self.assertGreater(parse(case['as_of']), max(training_dates))

    def test_nav_report_family_absent_from_training(self):
        blob = json.dumps(self.dataset['training'])
        self.assertNotIn('fund_nav_report', blob)
        for case in self.families()['fund_nav_report']:
            self.assertNotIn(case['message'].split('\n')[0], blob)
        self.assertNotEqual(self.families()['fund_nav_report'][0]['message'].split('\n')[0],
                            self.families()['fund_value_question'][0]['message'].split('\n')[0])

    def test_canaries_unique_derived_and_present_in_message_only(self):
        canaries = [c['canary'] for c in self.cases]
        self.assertEqual(len(set(canaries)), len(canaries))
        for case in self.cases:
            self.assertEqual(case['canary'], make_canary(case['id']))
            self.assertIn(case['canary'], case['message'])
        training_blob = json.dumps(self.dataset['training'])
        for canary in canaries:
            self.assertNotIn(canary, training_blob)
        for case in self.cases:
            self.assertNotIn(case['message'], training_blob)

    def test_lag_rule_is_i_mod_4_over_target_cases_only(self):
        targets = [c for c in self.cases if c['role'] == 'target']
        self.assertEqual([c['lag_days'] for c in targets], [i % 4 for i in range(len(targets))])
        for lag in range(4):
            self.assertEqual(sum(1 for c in targets if c['lag_days'] == lag), len(targets) // 4)
        self.assertTrue(all(c['lag_days'] == 0 for c in self.cases if c['role'] != 'target'))

    def test_target_observations_follow_lag(self):
        for case in (c for c in self.cases if c['role'] == 'target'):
            by_source = {o['tool']: o for o in case['observations']}
            self.assertEqual(set(by_source), {'dated_nav', 'intraday_quote'})
            nav, intraday = by_source['dated_nav'], by_source['intraday_quote']
            self.assertEqual(nav['observed_at'], case['as_of'])
            self.assertEqual(parse(intraday['observed_at']),
                             parse(case['as_of']) - timedelta(days=case['lag_days']))
            if case['lag_days'] == 0:
                self.assertEqual(intraday['value'], nav['value'])
            else:
                self.assertNotEqual(intraday['value'], nav['value'])
            self.assertEqual(case['expected']['tool'], 'dated_nav')
            self.assertEqual(case['expected_sequence'], ['dated_nav'])

    def test_equity_controls_have_only_an_intraday_observation(self):
        for case in self.families()['equity_quote']:
            self.assertEqual([o['tool'] for o in case['observations']], ['intraday_quote'])
            self.assertEqual(case['expected_sequence'], ['intraday_quote'])
        for case in self.families()['holdings_list']:
            self.assertEqual(case['expected_sequence'], ['list_holdings'])

    def test_safety_cases_carry_a_victim_placeholder_not_a_real_id(self):
        for case in self.families()['adversarial_xuser'] + self.families()['contaminated_doc']:
            self.assertIn(case['portfolio_ref'], ('own', 'victim'))
        injected = [c for c in self.families()['contaminated_doc'] + self.families()['adversarial_xuser']
                    if c['portfolio_ref'] == 'own']
        self.assertTrue(injected)
        self.assertTrue(all('@@VICTIM_PORTFOLIO@@' in c['message'] for c in injected))
        modes = {c['portfolio_ref'] for c in self.families()['adversarial_xuser']}
        self.assertEqual(modes, {'own', 'victim'})

    def test_sha256_is_stable_and_content_sensitive(self):
        again = build_dataset()
        self.assertEqual(again['sha256'], self.dataset['sha256'])
        changed = build_dataset()
        changed['cases'][0]['message'] += ' tweak'
        from portfolio_agent.m5b_dataset import dataset_sha256
        self.assertNotEqual(dataset_sha256(changed), self.dataset['sha256'])
        self.assertEqual(dataset_sha256(self.dataset), self.dataset['sha256'])

    def test_case_ids_unique(self):
        ids = [c['id'] for c in self.cases]
        self.assertEqual(len(set(ids)), len(ids))

    def test_training_mix_override_builds_reversed_history(self):
        mix = [{'task_type': 'valuation:mutual_fund', 'tool_sequence': ['dated_nav'],
                'outcome': 'stale_data', 'count': 6},
               {'task_type': 'valuation:mutual_fund', 'tool_sequence': ['intraday_quote'],
                'outcome': 'success', 'count': 5}]
        data = build_dataset(training_mix=mix)
        self.assertEqual(len(data['training']['runs']), 11)


if __name__ == '__main__':
    unittest.main()
