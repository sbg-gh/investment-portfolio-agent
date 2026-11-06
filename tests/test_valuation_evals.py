import unittest
class ValuationEvalTests(unittest.TestCase):
    def test_fixture_baseline_and_mutation_detection(self):
        from portfolio_agent.valuation_evaluation import evaluate,load_cases
        cases=load_cases();report=evaluate(cases)
        self.assertGreaterEqual(report['total'],5)
        self.assertEqual(report['passed'],report['total'])
        self.assertEqual(len(report['dataset_sha256']),64)
        mutated=[{**c,'expected':{'status':'wrong','total':'999'}} for c in cases]
        self.assertEqual(evaluate(mutated)['passed'],0)
        with self.assertRaises(ValueError):evaluate([])
        with self.assertRaises(ValueError):evaluate([cases[0],cases[0]])
