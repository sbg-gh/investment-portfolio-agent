"""Guarded Ollama benchmark script, offline path written first.

The benchmark is the ONLY thing that may touch a live local model, and only behind a
reachability probe. With --dry-run (and whenever Ollama is unreachable) it must perform NO
network and write a clearly-labeled placeholder row into docs/M4.md, exiting 0 so CI never
depends on a live server.
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts' / 'benchmark_chat.py'


def _load_benchmark():
    spec = importlib.util.spec_from_file_location('benchmark_chat', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BenchmarkDryRunTests(unittest.TestCase):
    def test_dry_run_writes_placeholder_without_network(self):
        bench = _load_benchmark()

        def _boom(*args, **kwargs):  # any network attempt fails the test
            raise AssertionError('benchmark dry-run must not touch the network')

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / 'M4.md'
            bench.urllib.request.urlopen = _boom  # guard: no network allowed
            code = bench.main(['--dry-run', '--doc', str(doc)])
            self.assertEqual(code, 0)
            text = doc.read_text()
            self.assertIn('UNREACHABLE', text)
            self.assertIn('operator to fill', text)

    def test_unreachable_probe_falls_back_to_placeholder(self):
        bench = _load_benchmark()

        def _refuse(*args, **kwargs):
            raise OSError('connection refused')

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / 'M4.md'
            bench.urllib.request.urlopen = _refuse  # probe fails -> unreachable
            code = bench.main(['--doc', str(doc)])
            self.assertEqual(code, 0)
            self.assertIn('UNREACHABLE', doc.read_text())


if __name__ == '__main__':
    unittest.main()
