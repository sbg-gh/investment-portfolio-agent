# Verification report

Verified on 2026-11-06 with Python 3.11.12 on the local host.

- Nine regression tests passed, including cross-user denial, invalid-input rollback, 401 research members, 75-slot monitoring enforcement, scoped memory, persistence and paper-only gating.
- Offline demonstration passed in source form and via the installed console entry point.
- A wheel built successfully using locally available setuptools/wheel and installed into a temporary virtual environment without runtime dependencies.
- The first attempt to build inside the temporary environment lacked the wheel build tool; building with the host build tools and installing that wheel succeeded.
- ZIP CRC integrity, safe relative member paths, manifest hashes, Markdown links and extracted source tests/demo were checked by the packaging verifier.
- Generated caches, databases, virtual environments and build metadata are excluded.

CI is supplied but has not run on GitHub. Python 3.12/3.13 and the full OS matrix remain CI checks. No real providers, local model, authenticated service or investment-performance claims were tested. These are planned milestones.

MANIFEST.json hashes every packaged file except itself. The adjacent .zip.sha256 file hashes the complete archive.
