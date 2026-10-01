#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan Git snapshots and history without publishing matched data or raw logs."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "8.30.1"


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("staged", "working", "history"))
    args = parser.parse_args()
    root = Path(os.fsdecode(git("rev-parse", "--show-toplevel")).strip())
    os.chdir(root)
    executable = shutil.which("gitleaks")
    if not executable and (root / "data/tools/gitleaks").is_file():
        executable = str(root / "data/tools/gitleaks")
    if not executable:
        sys.exit(f"Install Gitleaks {VERSION} and add it to PATH first.")
    result = subprocess.run([executable, "version"], capture_output=True, text=True)
    if result.returncode or result.stdout.strip().lstrip("v") != VERSION:
        sys.exit(f"Gitleaks {VERSION} is required.")

    # Never put the actual identifiers in tracked configuration or CLI arguments.
    identifiers = set(os.environ.get("PRIVATE_IDENTIFIERS", "").splitlines())
    if os.environ.get("CI"):
        if not any(item.strip() for item in identifiers):
            sys.exit("CI requires the PRIVATE_IDENTIFIERS secret; scan was not performed.")
    else:
        identifiers.add(Path.home().name)
    identifiers = {item.strip() for item in identifiers if item.strip()}
    config = (root / ".gitleaks.toml").read_text()
    # Literal, case-insensitive substring matching also catches names inside paths
    # and compound identifiers. Hex escapes keep all input safe for Go regex/TOML.
    pattern = (
        "(?i)(?:"
        + "|".join(
            "".join(rf"\x{{{ord(char):x}}}" for char in item) for item in sorted(identifiers)
        )
        + ")"
    )
    config += (
        '\n[[rules]]\nid = "private-identifier"\ndescription = "Private identifier"\nregex = '
        + json.dumps(pattern)
        + "\n"
    )
    env = os.environ.copy()
    env["GITLEAKS_CONFIG_TOML"] = config
    # Do not inherit an unrelated local config or silently apply suppression files.
    env.pop("GITLEAKS_CONFIG", None)
    failed = False

    def check_identifiers(data: bytes) -> None:
        nonlocal failed
        text = data.decode("utf-8", errors="replace").casefold()
        if any(item.casefold() in text for item in identifiers):
            failed = True
            print("Blocked: private-identifier (content or filename)")

    with tempfile.TemporaryDirectory(prefix="sensitive-scan-") as temp_dir:
        temp = Path(temp_dir)
        report = temp / "report.json"
        ignore = temp / "empty-ignore"
        ignore.touch()

        def scan(command: list[str], data: bytes | None = None) -> None:
            nonlocal failed
            report.unlink(missing_ok=True)
            run = subprocess.run(
                [
                    executable,
                    *command,
                    "--redact=100",
                    "--no-banner",
                    "--ignore-gitleaks-allow",
                    "--gitleaks-ignore-path",
                    str(ignore),
                    "--report-format",
                    "json",
                    "--report-path",
                    str(report),
                ],
                input=data,
                capture_output=True,
                env=env,
            )
            if run.returncode:
                failed = True
                # Values, filenames, commit authors and raw tool errors can all be
                # private. Print only rule IDs and line numbers, even in CI.
                findings = json.loads(report.read_text()) if report.exists() else []
                for finding in findings or []:
                    print(f"Blocked: {finding['RuleID']} at line {finding['StartLine']}")
                if not findings:
                    print("Scan failed; check tool/configuration locally. Raw output withheld.")

        if args.mode == "history":
            # Enforce private names even where built-in Gitleaks allowlists would
            # normally exempt a lockfile or example. Include removed lines too.
            check_identifiers(
                git(
                    "log",
                    "--all",
                    "--full-history",
                    "--format=",
                    "--no-ext-diff",
                    "--no-textconv",
                    "-p",
                )
            )
            scan(["git", "--log-opts=--all --full-history", "."])
            # git patch scanning alone does not reliably check filenames.
            paths = git("log", "--all", "--format=", "--name-only")
            check_identifiers(paths)
            scan(["stdin"], paths)
        else:
            snapshot = temp / "snapshot"
            snapshot.mkdir()
            options = (
                ["--cached"]
                if args.mode == "staged"
                else ["--cached", "--others", "--exclude-standard"]
            )
            names = sorted(set(git("ls-files", "-z", *options).split(b"\0")) - {b""})
            for name in names:
                relative = Path(os.fsdecode(name))
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if args.mode == "staged":
                    content = git("show", ":" + os.fsdecode(name))
                else:
                    source = root / relative
                    if source.is_symlink():
                        content = os.fsencode(os.readlink(source))
                    elif source.is_file():
                        content = source.read_bytes()
                    elif not source.exists():
                        continue
                    else:
                        sys.exit("Unsupported tracked entry; scan was not completed.")
                check_identifiers(content)
                target.write_bytes(content)
            scan(["dir", str(snapshot)])
            check_identifiers(b"\n".join(names))
            scan(["stdin"], b"\n".join(names))
    if failed:
        sys.exit("Sensitive data check FAILED. Remove/anonymize data before publishing.")
    print(f"Sensitive data check passed ({args.mode}).")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError):
        sys.exit("Sensitive data check could not complete; raw error withheld.")
