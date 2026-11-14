#!/usr/bin/env bash
# Package the exact editorial matrix, with full staged and archived media QA.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python package_release.py "$@"
