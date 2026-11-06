#!/usr/bin/env python3
"""Guarded Ollama chat-latency benchmark. Never required by CI; never fails CI.

This is the ONLY component that may touch a live local model, and only behind a reachability
probe. When Ollama is reachable it runs N chat turns through the vendor-neutral
OpenAICompatibleModel against llama3.2:3b, measures wall-clock latency (p50/p95) and peak RSS
via resource.getrusage, and writes a Markdown results row into docs/M4.md. When unreachable
(or invoked with --dry-run) it writes a clearly-labeled 'UNREACHABLE' placeholder row and
exits 0 with NO network. Conservative Mac-mini bounds come from ModelConfig. Stdlib only.

Usage: PYTHONPATH=src python scripts/benchmark_chat.py [--turns N] [--dry-run] [--doc PATH]
"""
import argparse
from datetime import datetime, timezone
import resource
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Ollama defaults (see FEAT-001 findings): OpenAI-compatible endpoint + model name.
OLLAMA_BASE = 'http://localhost:11434'
OLLAMA_ENDPOINT = OLLAMA_BASE + '/v1'
OLLAMA_MODEL = 'llama3.2:3b'
DEFAULT_DOC = Path(__file__).resolve().parents[1] / 'docs' / 'M4.md'
TABLE_HEADER = (
    '| Timestamp (UTC) | Endpoint | Model | Turns | p50 (ms) | p95 (ms) | Peak RSS (MB) |\n'
    '| --- | --- | --- | --- | --- | --- | --- |')
SECTION_TITLE = '## Chat latency benchmark'


def _probe(timeout=1.0):
    """Short reachability probe. Any network error means unreachable; never raises."""
    try:
        with urllib.request.urlopen(OLLAMA_BASE, timeout=timeout):
            return True
    except Exception:
        return False


def _peak_rss_mb():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KB, macOS reports bytes; normalize to MB heuristically.
    return round(rss / (1024 * 1024), 1) if rss > 1024 * 1024 else round(rss / 1024, 1)


def _placeholder_row(reason):
    now = datetime.now(timezone.utc).isoformat()
    return (f'| {now} | {OLLAMA_ENDPOINT} | {OLLAMA_MODEL} | - | UNREACHABLE | UNREACHABLE | '
            f'UNREACHABLE | <!-- {reason}: operator to fill --> |')


def _measured_row(turns, latencies_ms):
    now = datetime.now(timezone.utc).isoformat()
    p50 = round(statistics.median(latencies_ms), 1)
    p95 = round(statistics.quantiles(latencies_ms, n=20)[-1], 1) if len(latencies_ms) > 1 \
        else round(latencies_ms[0], 1)
    return (f'| {now} | {OLLAMA_ENDPOINT} | {OLLAMA_MODEL} | {turns} | {p50} | {p95} | '
            f'{_peak_rss_mb()} |')


def _write_row(doc_path, row):
    """Append a results row under the benchmark section, creating the table if absent."""
    path = Path(doc_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ''
    if SECTION_TITLE not in existing:
        prefix = existing if existing.endswith('\n') or existing == '' else existing + '\n'
        existing = prefix + f'\n{SECTION_TITLE}\n\n{TABLE_HEADER}\n'
    path.write_text(existing + row + '\n')


def _run_turns(turns):
    """Run N real chat turns through the OpenAI-compatible adapter; return latencies (ms)."""
    # Imported lazily so the offline/dry-run path never requires PYTHONPATH=src.
    from portfolio_agent.model import ModelConfig, OpenAICompatibleModel
    config = ModelConfig.from_env(env={'MODEL_ENDPOINT': OLLAMA_ENDPOINT,
                                       'MODEL_NAME': OLLAMA_MODEL})
    model = OpenAICompatibleModel(config)
    prompt = ('You are a read-only portfolio assistant. Reply with a short JSON object '
              '{"explanation": "...", "tool_calls": []}.')
    latencies = []
    for _ in range(turns):
        start = time.perf_counter()
        model.generate(prompt, max_tokens=config.max_output_tokens)
        latencies.append((time.perf_counter() - start) * 1000)
    return latencies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--turns', type=int, default=5, help='number of chat turns to time')
    parser.add_argument('--dry-run', action='store_true',
                        help='skip the probe and write the UNREACHABLE placeholder')
    parser.add_argument('--doc', default=str(DEFAULT_DOC),
                        help='Markdown file to write the results table into')
    args = parser.parse_args(argv)

    if args.dry_run:
        _write_row(args.doc, _placeholder_row('dry-run'))
        print('benchmark: dry-run; wrote UNREACHABLE placeholder (no network).')
        return 0
    if not _probe():
        _write_row(args.doc, _placeholder_row('unreachable'))
        print('benchmark: Ollama unreachable; wrote UNREACHABLE placeholder (no network).')
        return 0
    try:
        latencies = _run_turns(args.turns)
        _write_row(args.doc, _measured_row(args.turns, latencies))
        print(f'benchmark: recorded {args.turns} turns against {OLLAMA_ENDPOINT}.')
    except Exception as exc:  # a live run must never fail CI
        _write_row(args.doc, _placeholder_row(f'error:{type(exc).__name__}'))
        print(f'benchmark: live run failed ({exc}); wrote placeholder.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
