# SPDX-License-Identifier: GPL-3.0-or-later
import json
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("restart", [False, True])
def test_missing_temporary_host_starts_fresh_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restart: bool
) -> None:
    start = runpy.run_path(str(Path(__file__).parents[1] / "scripts/live-start.py"))["start"]
    env = tmp_path / "live.env"
    env.write_text("SDR_PCSC_DIR=/placeholder\n")
    state = tmp_path / ".live-start-test"
    state.mkdir()
    current = state / "current.json"
    current.write_text(json.dumps({"host": str(tmp_path / "removed")}))
    config = {"services": {"app": {"volumes": [{"source": "device", "target": "/device"}]}}}
    monkeypatch.setattr("subprocess.check_output", lambda *a, **kw: json.dumps(config))
    # Stop at the privilege check; neither sudo nor host preparation is executed.
    run = Mock(return_value=Mock(returncode=1))
    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(RuntimeError, match="sudo -v"):
        start(env, "test", restart)
    assert run.call_args.args[0] == ["sudo", "-n", "true"]
    assert json.loads(current.read_text())["host"] == str(tmp_path / "removed")
