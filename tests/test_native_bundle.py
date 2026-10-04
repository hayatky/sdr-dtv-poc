# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import hashlib
import json
from pathlib import Path

import pytest

from sdr_dtv_poc.live_sources import verify


def test_bundled_receiver_is_complete_verified_and_compilable() -> None:
    root = Path(__file__).parents[1] / "native"
    manifest = json.loads((root / "manifest.json").read_text())
    assert len(manifest) == 14
    assert set(manifest) == {p.relative_to(root).as_posix() for p in root.rglob("*.py")}
    for name, entry in manifest.items():
        raw = (root / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], name
        ast.parse(raw, filename=name)
    verify(root / "receiver")


def test_modified_receiver_cannot_pass_verification(tmp_path: Path) -> None:
    import shutil

    source = Path(__file__).parents[1] / "native/receiver"
    shutil.copytree(source, tmp_path / "receiver")
    (tmp_path / "receiver/src/receiver/worker.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(tmp_path / "receiver")
