#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
cd "$(git rev-parse --show-toplevel)"
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python scripts/check-sensitive.py working
uv run --locked python scripts/check-sensitive.py history
