# SPDX-License-Identifier: GPL-3.0-or-later
"""Start the temporary host preparation and Compose WebUI from an existing live env file."""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


def start(env_file: Path, project: str, restart_host: bool = False) -> None:
    repo = Path(__file__).resolve().parents[1]
    env_file = env_file.resolve(strict=True)
    state = env_file.parent / f".live-start-{project}"
    state.mkdir(mode=0o700, exist_ok=True)
    with (state / "launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compose = ["docker", "compose", "-p", project, "-f", str(repo / "compose.live.yaml")]
        config = json.loads(
            subprocess.check_output(
                [*compose, "--env-file", str(env_file), "config", "--format", "json"], timeout=15
            )
        )
        volumes = config["services"]["app"]["volumes"]
        device = next(v["source"] for v in volumes if v["target"] == "/device")
        current = state / "current.json"
        active = json.loads(current.read_text()) if current.exists() else None
        fingerprint = hashlib.sha256(env_file.read_bytes()).hexdigest()
        if active and restart_host and not (Path(active["host"]) / "host-cleanup").exists():
            (Path(active["host"]) / "host-stop").touch()
            deadline = time.monotonic() + 45
            while not (Path(active["host"]) / "host-cleanup").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "ホスト準備の終了待ちです。視聴・録画を停止してから再試行してください。"
                    )
                time.sleep(0.2)
        if active and not (Path(active["host"]) / "host-cleanup").exists():
            if active.get("source") != str(env_file) or active.get("sha256") != fingerprint:
                raise RuntimeError(
                    "設定ファイルが変わっています。視聴・録画を停止し、同じコマンドに --restart-host を付けて再実行してください。"
                )
            host = Path(active["host"])
            if not (host / "host-ready").exists() or not (host / "pcsc/pcscd.comm").is_socket():
                raise RuntimeError(
                    "前のホスト準備の状態を確認できません。host-stopで停止してから再実行してください。"
                )
            runtime_env = Path(active["env"])
        else:
            if subprocess.run(
                ["sudo", "-n", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).returncode:
                raise RuntimeError(
                    "Ubuntu側で sudo -v を実行してから、このコマンドをもう一度実行してください。"
                )
            # UNIX socket paths have a short platform limit, independent of
            # the checkout/environment-file path length.
            host = Path(tempfile.mkdtemp(prefix="sdr-dtv-host-", dir="/tmp"))
            runtime_env = host / "live.env"
            # Keep the administrator's configuration, overriding only the private
            # socket directory. No shell eval or application-provided commands.
            text = "\n".join(
                line
                for line in env_file.read_text().splitlines()
                if not line.startswith("SDR_PCSC_DIR=")
            )
            runtime_env.write_text(text + "\nSDR_PCSC_DIR=" + str(host / "pcsc") + "\n")
            runtime_env.chmod(0o600)
            with (host / "host.log").open("xb") as log:
                process = subprocess.Popen(
                    [
                        "sudo",
                        "-n",
                        "bash",
                        str(repo / "scripts/live-host-window.sh"),
                        str(host),
                        device,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            deadline = time.monotonic() + 15
            while not (host / "host-ready").exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    (host / "host-stop").touch()
                    raise RuntimeError(
                        "ホスト準備に失敗しました。USB接続と既存の受信処理を確認してください。詳細は起動用フォルダーのhost.logにあります。"
                    )
                time.sleep(0.2)
            current.write_text(
                json.dumps(
                    {
                        "host": str(host),
                        "env": str(runtime_env),
                        "source": str(env_file),
                        "sha256": fingerprint,
                    }
                )
            )
        subprocess.run(
            [*compose, "--env-file", str(runtime_env), "up", "-d", "--build"], check=True
        )
        print("WebUI: http://localhost:18335/ （受信は開始していません）")
        print("復旧が必要な場合は、WebUIの「受信機を確認して復旧する」を押してください。")
        print("ホスト準備は約57分で終了します。終了後は同じ起動コマンドで再開できます。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--project", default="sdr-stage3-live")
    parser.add_argument("--restart-host", action="store_true")
    args = parser.parse_args()
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.project):
        parser.error("invalid project name")
    if os.geteuid() == 0:
        parser.error("通常ユーザーで実行してください。")
    try:
        start(args.env_file, args.project, args.restart_host)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        if isinstance(exc, RuntimeError):
            print(str(exc))
        else:
            print("起動できませんでした。設定ファイル、Docker、他の起動処理を確認してください。")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
