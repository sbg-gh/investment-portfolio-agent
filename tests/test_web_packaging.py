"""Packaging guard: the core stays dependency-free; the web UI is an opt-in extra."""
import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = ('uvicorn', 'h11', 'click', 'starlette', 'fastapi', 'pydantic', 'anyio')
IMPORTS = ('portfolio_agent', 'portfolio_agent.cli', 'portfolio_agent.services',
           'portfolio_agent.chat', 'portfolio_agent.evaluation', 'portfolio_agent.m6_evaluation', 'portfolio_agent.web_security_evaluation',
           'portfolio_agent.valuation_evaluation')


class PackagingTests(unittest.TestCase):
    def pyproject(self):
        return tomllib.loads((ROOT / 'pyproject.toml').read_text())

    def test_core_dependencies_stay_empty(self):
        self.assertEqual(self.pyproject()['project']['dependencies'], [])

    def test_ui_extra_pins_every_package_exactly(self):
        extra = self.pyproject()['project']['optional-dependencies']['ui']
        self.assertEqual(sorted(extra), ['click==8.5.1', 'h11==0.16.0', 'uvicorn==0.54.0'])

    def test_static_assets_are_package_data_but_not_tracked(self):
        data = self.pyproject()['tool']['setuptools']['package-data']['portfolio_agent']
        self.assertIn('web/static/**', data)
        ignored = (ROOT / '.gitignore').read_text().splitlines()
        self.assertIn('src/portfolio_agent/web/static/', ignored)
        self.assertIn('ui/node_modules/', ignored)

    def test_hashed_lock_covers_exact_pins(self):
        lock = (ROOT / 'requirements' / 'ui.lock').read_text()
        for pin in ('uvicorn==0.54.0', 'h11==0.16.0', 'click==8.5.1'):
            self.assertRegex(lock, re.escape(pin) + r'\s*\\?\s*\n\s*--hash=sha256:[0-9a-f]{64}')
        pins = re.findall(r'^[A-Za-z0-9_.-]+==\S+', lock, re.M)
        self.assertGreaterEqual(len(pins), 3)
        # every pinned package block carries at least one hash
        blocks = re.split(r'^(?=[A-Za-z0-9_.-]+==)', lock, flags=re.M)
        for block in blocks:
            if re.match(r'[A-Za-z0-9_.-]+==', block):
                self.assertIn('--hash=sha256:', block)

    def test_core_cli_and_evals_import_without_third_party_packages(self):
        code = ('import sys\n'
                f'for name in {BLOCKED!r}: sys.modules[name] = None\n'
                f'import importlib\nfor name in {IMPORTS!r}: importlib.import_module(name)\n'
                'print("ok")')
        done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                              text=True, env={'PYTHONPATH': str(ROOT / 'src'), 'PATH': ''})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), 'ok')


if __name__ == '__main__':
    unittest.main()
