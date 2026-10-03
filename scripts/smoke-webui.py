# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Chromium UI integration check with an isolated, owned synthetic API.

Run with Playwright 1.58.0 via uv --no-project --with, not a product dependency.
The input directory must contain demo.ts and demo-14.ts (360 seconds each).
The data directory must not exist. No existing server or data is stopped/deleted.
"""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import expect, sync_playwright  # type: ignore[import-not-found]

TERMINAL = {"completed", "failed", "interrupted"}


class Server:
    def __init__(self, source: Path, data: Path, port: int):
        if data.exists():
            raise ValueError("Use a new data directory")
        data.mkdir(parents=True)
        self.origin = f"http://localhost:{port}"
        self.port = port
        self.env = {
            **os.environ,
            "SDR_DEMO_PATH": str(source.resolve()),
            "SDR_DATA_DIR": str(data.resolve()),
            "SDR_ORIGIN": self.origin,
            "SDR_DEVICE_LOCK_DIR": str((data / "lock").resolve()),
        }
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, request: Any) -> None:
        try:
            response = request.get(self.origin + "/api/health", timeout=500)
        except Exception:
            response = None
        if response is not None:
            raise AssertionError("Port already in use; leave that server untouched")
        self.process = subprocess.Popen(
            [
                "uv",
                "run",
                "--locked",
                "uvicorn",
                "sdr_dtv_poc.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--workers",
                "1",
                "--no-proxy-headers",
                "--no-access-log",
                "--timeout-graceful-shutdown",
                "10",
            ],
            env=self.env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if self.process.poll() is not None:
                raise AssertionError("Owned API did not start")
            try:
                if request.get(self.origin + "/api/health", timeout=500).ok:
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise AssertionError("API startup deadline")

    def stop(self, hard: bool = False) -> None:
        if self.process and self.process.poll() is None:
            # The process group was created by this instance, never an existing API.
            os.killpg(self.process.pid, signal.SIGKILL if hard else signal.SIGTERM)
            self.process.wait(timeout=35)


def click(page: Any, name: str) -> None:
    page.get_by_role("button", name=name, exact=False).first.click()


def read(page: Any, path: str) -> Any:
    response = page.request.get(path)
    assert response.ok
    return response.json()


def wait_read(page: Any, path: str, predicate: Any, timeout: int = 20000) -> Any:
    end = time.monotonic() + timeout / 1000
    while time.monotonic() < end:
        value = read(page, path)
        if predicate(value):
            return value
        page.wait_for_timeout(100)
    raise AssertionError("API observation deadline")


def av(page: Any, label: str, warmup: int = 0) -> dict[str, Any]:
    video = page.get_by_label(label)
    # Arrow functions avoid Playwright's string-expression eval under the CSP.
    page.wait_for_function(
        "label => document.querySelector(`video[aria-label='${label}']`).readyState >= 2 && !document.querySelector(`video[aria-label='${label}']`).error",
        arg=label,
        timeout=20000,
    )
    page.wait_for_timeout(1200)
    video.evaluate("v => v.play()")
    value: dict[str, Any] = video.evaluate(
        """async (v, warmup) => {
      const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
      const context = new AudioContext(), analyser = context.createAnalyser();
      const gain = context.createGain(); gain.gain.value = 0;
      context.createMediaElementSource(v).connect(analyser);
      analyser.connect(gain); gain.connect(context.destination); await context.resume();
      const pcm = new Float32Array(analyser.fftSize); let peak = 0;
      const timer = setInterval(() => {
        analyser.getFloatTimeDomainData(pcm);
        for (const sample of pcm) peak = Math.max(peak, Math.abs(sample));
      }, 20);
      try {
        await delay(warmup);
        const samples = [];
        for(let i=0;i<5;i++) { await delay(1000); samples.push(v.currentTime); }
        return {frames:v.getVideoPlaybackQuality().totalVideoFrames, audio_peak:peak,
          samples, muted:v.muted, observation:v.sdrObservation};
      } finally { clearInterval(timer); v.sdrTestAudio = context; }
    }""",
        warmup,
    )
    assert value["frames"] > 0 and value["audio_peak"] > 0.001 and not value["muted"]
    assert value["samples"][-1] - value["samples"][0] > 3
    return value


def exercise(
    page: Any, context: Any, server: Server, source: Path, images: Path | None
) -> dict[str, Any]:
    origin = server.origin
    posts: list[dict[str, Any]] = []
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "request",
        lambda req: (
            posts.append({"path": req.url.removeprefix(origin), "body": req.post_data_json})
            if req.method == "POST"
            else None
        ),
    )
    page.goto(origin)
    expect(page.get_by_text("API接続", exact=True)).to_be_visible()
    click(page, "接続を確認する")
    expect(page.get_by_text("使用できます").first).to_be_visible()
    assert not posts
    # Verify actual OpenAPI, not the mock's former JSON shape.
    schema = read(page, origin + "/openapi.json")
    assert "/api/status" not in schema["paths"]
    assert "service_key" in schema["components"]["schemas"]["SessionStart"]["properties"]
    click(page, "スキャンを開始する")
    expect(
        page.get_by_text("スキャンが終わりました。2局を保存しました。", exact=True).first
    ).to_be_visible(timeout=20000)
    assert posts[-1]["body"]["channels"] == [13, 14]
    assert posts[-1]["body"]["input_kind"] == "synthetic"
    services = read(page, origin + "/api/services")
    assert len(services) == 2 and all(not s["current_reception"] for s in services)
    # Observe and cancel a new scan, preserving already saved stations.
    click(page, "スキャンを開始する")
    click(page, "スキャンを中止する")
    wait_read(page, origin + "/api/scans", lambda xs: all(x["state"] in TERMINAL for x in xs))
    assert len(read(page, origin + "/api/services")) == 2
    page.wait_for_timeout(1200)
    click(page, "視聴する")
    video = page.get_by_label("視聴プレイヤー")
    video.evaluate("v => { window.initialVideo = v; }")
    wait_read(page, origin + "/api/sessions", lambda xs: any(x["bytes_received"] > 0 for x in xs))
    expect(page.get_by_role("button", name="録画を開始する（最大5分）", exact=False)).to_be_enabled(
        timeout=15000
    )
    click(page, "録画を開始する（最大5分）")
    expect(page.get_by_role("button", name="録画を停止する", exact=True)).to_be_visible()
    assert page.locator(".station:enabled").count() == 0
    live = av(page, "視聴プレイヤー", 7000)
    assert video.evaluate("v => v === window.initialVideo")
    session = next(x for x in read(page, origin + "/api/sessions") if x["state"] == "running")
    assert session["id"] == live["observation"]["session_id"]
    assert (
        live["observation"]["playing_at"]
        and session["ts_started_at"]
        and session["hls"]["ready_at"]
    )
    start_body = next(x["body"] for x in posts if x["path"] == "/api/sessions")
    assert start_body["service_key"] in {s["id"] for s in services}
    assert start_body["duration_seconds"] == 600 and start_body["enable_hls"]
    if images:
        page.evaluate("() => scrollTo(0, 0)")
        page.screenshot(path=str(images / "watch.png"), full_page=True)
    click(page, "録画を停止する")
    recording = wait_read(
        page, origin + "/api/recordings", lambda xs: xs[-1]["state"] == "completed"
    )[-1]
    before = video.evaluate("v => v.currentTime")
    page.wait_for_timeout(2500)
    assert video.evaluate("v => v.currentTime") > before + 1
    # Reload and a second tab only read the existing session/recording.
    count = len(posts)
    video.evaluate("v => v.sdrTestAudio?.close()")
    page.reload()
    page.wait_for_timeout(1800)
    assert len(posts) == count
    other = context.new_page()
    other.goto(origin + "/#watch")
    other.wait_for_timeout(1500)
    assert len(read(page, origin + "/api/sessions")) == 1
    assert len(read(page, origin + "/api/recordings")) == 1
    other.close()
    # Real offline mode: recover reads without automatically starting anything.
    context.set_offline(True)
    expect(page.get_by_text("サーバーと通信できません", exact=True).first).to_be_visible(
        timeout=10000
    )
    context.set_offline(False)
    click(page, "今すぐ再接続する")
    expect(page.get_by_role("button", name="受信を停止する", exact=True)).to_be_enabled(
        timeout=10000
    )
    assert len(posts) == count
    click(page, "受信を停止する")
    wait_read(page, origin + "/api/sessions", lambda xs: all(x["state"] in TERMINAL for x in xs))
    page.get_by_role("tab", name="録画", exact=True).click()
    click(page, "再生する")
    recorded = av(page, "録画プレイヤー")
    with page.expect_download() as event:
        page.get_by_role("link", name="TSファイルをダウンロード", exact=True).first.click()
    downloaded = Path(event.value.path()).read_bytes()
    offset = recording["source_offset_bytes"]
    with source.open("rb") as file:
        file.seek(offset)
        expected = file.read(recording["bytes_written"])
    assert hashlib.sha256(downloaded).digest() == hashlib.sha256(expected).digest()
    assert len(downloaded) == recording["bytes_written"]
    if images:
        page.evaluate("() => scrollTo(0, 0)")
        page.screenshot(path=str(images / "recordings.png"), full_page=True)
        page.set_viewport_size({"width": 375, "height": 812})
        page.screenshot(path=str(images / "mobile.png"), full_page=True)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1280, "height": 900})
    page.get_by_label("録画プレイヤー").evaluate("v => v.sdrTestAudio?.close()")
    click(page, "再生を閉じる")
    # Select another station; original video DOM survives tab and width changes.
    page.get_by_role("tab", name="視聴", exact=True).click()
    page.locator(".station").filter(has_text="Synthetic Test 14").click()
    second = wait_read(
        page,
        origin + "/api/sessions",
        lambda xs: any(
            x["state"] == "running" and x["service"]["physical_channel"] == 14 for x in xs
        ),
    )[-1]
    expect(page.locator("#now-title")).to_have_text("Synthetic Test 14")
    click(page, "録画を開始する（最大5分）")
    page.wait_for_timeout(1200)
    click(page, "受信を停止する")
    click(page, "録画と受信を停止する")
    partial = wait_read(page, origin + "/api/recordings", lambda xs: xs[-1]["partial"])[-1]
    assert partial["state"] == "failed" and partial["end_reason"] == "source_ended"
    page.get_by_role("tab", name="録画", exact=True).click()
    row = page.locator(".rec-item").filter(has_text="途中で終了").last
    expect(row.get_by_role("button", name="再生する", exact=True)).to_be_disabled()
    expect(row.get_by_role("button", name="TSファイルをダウンロード", exact=True)).to_be_disabled()
    assert second["id"] != session["id"]
    assert not errors, errors
    return {
        "live": live,
        "recorded": recorded,
        "download_bytes": len(downloaded),
        "ui_posts": len(posts),
        "human_playback": "not_checked",
    }


def faults(page: Any, context: Any, server: Server) -> dict[str, Any]:
    origin = server.origin
    page.get_by_role("tab", name="視聴", exact=True).click()
    wait_read(page, origin + "/api/sessions", lambda xs: all(x["state"] in TERMINAL for x in xs))
    page.wait_for_timeout(1300)
    # The server accepts selection but its response is lost. Reads recover it.
    lost: list[str] = []

    def drop_response(route: Any) -> None:
        if route.request.method == "POST" and not lost:
            response = route.fetch()
            assert response.status == 202
            lost.append(response.json()["id"])
            route.abort()
        else:
            route.continue_()

    page.route("**/api/sessions", drop_response)
    page.locator(".station").filter(has_text="Synthetic Test 13").click()
    page.wait_for_timeout(1000)
    page.unroute("**/api/sessions", drop_response)
    expect(page.get_by_role("button", name="受信を停止する", exact=True)).to_be_enabled(
        timeout=10000
    )
    assert len(lost) == 1
    assert (
        page.evaluate(
            "() => Object.keys(JSON.parse(sessionStorage.getItem('sdr-uncertain') || '{}')).length"
        )
        == 0
    )
    click(page, "受信を停止する")
    wait_read(page, origin + "/api/sessions", lambda xs: all(x["state"] in TERMINAL for x in xs))
    page.wait_for_timeout(1300)
    page.locator(".station").filter(has_text="Synthetic Test 13").click()
    sessions = wait_read(
        page, origin + "/api/sessions", lambda xs: any(x["state"] == "running" for x in xs)
    )
    current = next(x for x in sessions if x["state"] == "running")
    assert current["id"] != lost[0]
    # API refusal reasons remain visible; none may turn into simulated success.
    for code, text in [
        ("insufficient_session_time", "録画に必要な入力の残り時間が足りません"),
        ("storage_full", "保存先の空き容量が足りません"),
        ("recording_busy", "録画中です"),
    ]:

        def refuse(route: Any, _request: Any, failure: str = code) -> None:
            if route.request.method == "POST":
                route.fulfill(
                    status=409, json={"code": failure, "message": "Operation unavailable"}
                )
            else:
                route.continue_()

        page.route("**/api/recordings", refuse)
        click(page, "録画を開始する（最大5分）")
        expect(page.get_by_text(text, exact=True).first).to_be_visible()
        page.unroute("**/api/recordings", refuse)
    click(page, "録画を開始する（最大5分）")
    page.wait_for_timeout(1500)
    # Restart the actual owned server while a recording is active. Keep the old
    # token until an explicit mutation receives 403, then allow bootstrap refresh.
    old_boot = read(page, origin + "/api/bootstrap")

    def old_token(route: Any) -> None:
        route.fulfill(json=old_boot)

    page.route("**/api/bootstrap", old_token)
    server.stop(hard=True)
    server.start(context.request)
    page.wait_for_timeout(2500)
    records = read(page, origin + "/api/recordings")
    assert records[-1]["state"] == "interrupted" and records[-1]["partial"]
    assert records[-1]["end_reason"] == "server_restart"
    before = len(read(page, origin + "/api/sessions"))
    page.locator(".station").filter(has_text="Synthetic Test 14").click()
    expect(page.get_by_text("接続情報を更新しました", exact=True)).to_be_visible()
    page.unroute("**/api/bootstrap", old_token)
    page.wait_for_timeout(5500)
    assert len(read(page, origin + "/api/sessions")) == before
    page.locator(".station").filter(has_text="Synthetic Test 14").click()
    wait_read(page, origin + "/api/sessions", lambda xs: any(x["state"] == "running" for x in xs))
    # Freeze a read response, then pagehide. The old finally must not restart a
    # timer. pageshow resumes one read loop and no mutation.
    held: list[Any] = []

    def hold(route: Any) -> None:
        if route.request.method == "GET":
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/sessions", hold)
    page.wait_for_timeout(1800)
    assert held
    page.evaluate("() => dispatchEvent(new PageTransitionEvent('pagehide'))")
    for route in held:
        try:
            route.abort()
        except Exception:
            pass
    page.unroute("**/api/sessions", hold)
    gets: list[str] = []
    page.on(
        "request",
        lambda req: gets.append(req.url) if req.method == "GET" and "/api/" in req.url else None,
    )
    page.wait_for_timeout(5500)
    assert not gets
    assert page.get_by_label("視聴プレイヤー").evaluate("v => !v.getAttribute('src') && v.paused")
    page.evaluate("() => dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    page.wait_for_timeout(2500)
    assert 1 <= sum(url.endswith("/api/sessions") for url in gets) <= 3
    assert len(read(page, origin + "/api/sessions")) == before + 1
    click(page, "受信を停止する")
    page.get_by_role("tab", name="録画", exact=True).click()
    playback_posts: list[str] = []

    def missing_playback(route: Any) -> None:
        if route.request.method == "POST":
            playback_posts.append("missing")
            route.abort()
        else:
            route.fulfill(status=404, json={"code": "playback_not_started"})

    page.route("**/api/recordings/*/playback", missing_playback)
    page.locator(".rec-item button:enabled").filter(has_text="再生する").first.click()
    page.wait_for_timeout(6000)
    assert playback_posts == ["missing"]
    assert (
        page.locator(".global-notices").get_by_text("サーバーと通信できません", exact=True).count()
        == 0
    )
    page.unroute("**/api/recordings/*/playback", missing_playback)
    complete = next(r for r in read(page, origin + "/api/recordings") if r["state"] == "completed")

    def failed_playback(route: Any) -> None:
        if route.request.method == "POST":
            playback_posts.append("failed")
        route.fulfill(
            status=202 if route.request.method == "POST" else 200,
            json={
                "id": "synthetic-failure",
                "recording_id": complete["id"],
                "state": "failed",
                "error_stage": "cas",
                "error_code": "cas_unavailable",
                "url": None,
            },
        )

    page.route("**/api/recordings/*/playback", failed_playback)
    page.locator(".rec-item button:enabled").filter(has_text="再生する").first.click()
    expect(page.get_by_text("録画の再生用ファイルを作れませんでした", exact=True)).to_be_visible()
    page.wait_for_timeout(2200)
    assert playback_posts == ["missing", "failed"]
    assert read(page, origin + "/api/recordings/" + complete["id"])["state"] == "completed"
    page.unroute("**/api/recordings/*/playback", failed_playback)
    click(page, "再生を閉じる")
    return {
        "playback_failure_separate": True,
        "lost_response_reconciled": True,
        "refusals": 3,
        "api_restart_403": True,
        "page_lifecycle": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18330)
    parser.add_argument("--images", type=Path)
    args = parser.parse_args()
    if args.images:
        args.images.mkdir(parents=True, exist_ok=False)
    for path in (args.source, args.source.with_stem(args.source.stem + "-14")):
        metadata = json.loads(path.with_suffix(".json").read_text())
        assert metadata["duration_seconds"] == 360
    server = Server(args.source, args.data_dir, args.port)
    with sync_playwright() as pw:
        request = pw.request.new_context()
        browser = None
        try:
            server.start(request)
            browser = pw.chromium.launch(executable_path=str(args.chromium), headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            try:
                result = exercise(page, context, server, args.source, args.images)
                result["faults"] = faults(page, context, server)
                unit_page = context.new_page()
                unit_page.goto(server.origin + "/?mode=mock")
                unit_page.wait_for_function("() => Boolean(window.SdrPlayer)")
                result["controlled_regressions"] = unit_page.evaluate(
                    Path("tests/webui-lifecycle.js").read_text()
                )
                unit_page.close()
            except Exception:
                # Do not publish page contents, private paths or API response bodies.
                print("WebUI verification failed; inspect locally and report only the cause.")
                raise
            print(json.dumps({"browser": browser.version, **result}, indent=2))
        finally:
            if browser is not None:
                browser.close()
            server.stop()
            request.dispose()


if __name__ == "__main__":
    main()
