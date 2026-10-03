# sdr-dtv-poc
Experimental SDR digital TV receiver PoC with a web UI for channel scanning, viewing, and short TS recordings.

## Before contributing

Install uv 0.12.18 and Gitleaks 8.30.1, then prepare the Python 3.12 environment:

```sh
uv sync --locked
git config --local core.hooksPath .githooks
sh scripts/check.sh
```

Python, dependencies, Ruff, mypy, and pytest are configured in
`.python-version`, `pyproject.toml`, and `uv.lock`. The shared check command runs
lint, formatting checks, type checks, synthetic tests, and sensitive data scans.
See [the development guide](docs/development.md) for individual commands and
native dependency boundaries. The receiver application is not implemented yet.

See [the sensitive data workflow](docs/sensitive-data.md) for setup, private home
name detection, CI configuration, and checks before publishing.

## License

Copyright (c) 2026 hayatky

This project's original code is licensed under the GNU General Public License,
version 3 or (at your option) any later version (`GPL-3.0-or-later`). See
[LICENSE](LICENSE) for the full license text.

Third-party components retain their respective copyright notices and licenses.
When distributing binaries or container images containing GPL-covered software,
provide the corresponding source, including modifications and build instructions,
in accordance with the applicable license. Changing this project's license does
not by itself complete those distribution requirements.

Use `SPDX-License-Identifier: GPL-3.0-or-later` in new original source files.
