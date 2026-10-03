// SPDX-License-Identifier: GPL-3.0-or-later
// Display-only mock backend for the WebUI (#27/#28).
// It never calls fetch/XMLHttpRequest and never starts a receiver. All station
// names, channels and recordings below are fictional and made for this project.
// Field names follow src/sdr_dtv_poc/models.py; fields marked "provisional" are
// UI proposals that the backend has not defined yet (see docs/webui-design.md).
(function () {
  'use strict';

  const SESSION_LIMIT = 600;
  const SCAN_LIMIT = 180;
  const RECORDING_LIMIT = 300;
  const STOP_GRACE = 5;
  const SCAN_SECONDS_PER_CHANNEL = 1;

  const SCENARIOS = [
    {id: 'normal', label: '通常（保存された局と録画あり）'},
    {id: 'first_run', label: '初回（保存された局・録画なし）'},
    {id: 'scan_none', label: 'スキャンで局が見つからない'},
    {id: 'scan_fail', label: 'スキャンが途中で失敗する'},
    {id: 'restore_unknown', label: '受信機の設定を戻せたか未確認'},
    {id: 'storage_low', label: '保存先の空き容量が不足'},
    {id: 'offline', label: 'サーバーと通信できない'},
    {id: 'cas_fail', label: 'カードによる復号に失敗する'},
    {id: 'transcode_fail', label: '映像の変換に失敗する'},
    {id: 'player_fail', label: 'ブラウザーで再生できない'},
    {id: 'short_session', label: '受信の残り時間が少ない'},
    {id: 'recording_partial', label: '録画が容量不足で途中終了する'},
  ];

  // Fictional services. Channel and remote-control numbers are arbitrary and do
  // not describe any real region. One name contains markup-like characters to
  // check that the UI renders names as plain text.
  const LIVE_SERVICES = [
    {channel: 15, sid: 4101, remote: 3, name: '架空テレビA'},
    {channel: 15, sid: 4102, remote: 3, name: '架空テレビA サブチャンネル'},
    {channel: 19, sid: 4201, remote: 5, name: '架空みなと放送'},
    {channel: 33, sid: 4301, remote: 7, name: '<b>架空放送C</b> & "表示確認"'},
    {channel: 41, sid: 4401, remote: null, name: null},
    {channel: 47, sid: 4501, remote: 11, name: '架空テレビD 局名がとても長い場合の折り返し表示を確認するための名前'},
  ];

  function uuid() {
    return crypto.randomUUID();
  }

  function fail(status, code) {
    return Promise.reject({status, code, message: 'Operation unavailable'});
  }

  function copy(value) {
    return value === null || value === undefined ? value : JSON.parse(JSON.stringify(value));
  }

  function createMockBackend(initial) {
    let scenario = initial && SCENARIOS.some(item => item.id === initial.scenario) ? initial.scenario : 'normal';
    let speed = initial && initial.speed ? initial.speed : 1;
    // Mock clock: wall time at reset plus accelerated elapsed time.
    let base = Date.now();
    let started = performance.now();
    let world;

    function now() {
      return base + (performance.now() - started) * speed;
    }
    function iso(ms) {
      return new Date(ms).toISOString();
    }
    function has(name) {
      return scenario === name;
    }
    function delay(seconds) {
      // Network and processing delay in real time, independent of the speed.
      return new Promise(resolve => setTimeout(resolve, seconds * 1000));
    }
    async function call(seconds, work) {
      await delay(seconds);
      if (has('offline')) return fail(0, 'network_error');
      return work();
    }

    function liveService(item, detectedAt, scanId) {
      return {
        id: `live-${item.channel}-${item.sid}`,
        name: item.name,
        input_kind: 'live',
        physical_channel: item.channel,
        service_id: item.sid,
        detection_stage: 'ts_si',
        remote_control_key: item.remote, // provisional
        detected_at: iso(detectedAt), // provisional
        scan_id: scanId, // provisional
      };
    }

    function fileServices() {
      return [
        {id: 'demo', name: 'Synthetic Test', input_kind: 'synthetic', physical_channel: null,
          service_id: 1, detection_stage: 'synthetic_definition', remote_control_key: null,
          detected_at: null, scan_id: null},
        {id: 'local_sample', name: '保存TSの例（架空）', input_kind: 'saved_ts',
          physical_channel: null, service_id: 1, detection_stage: 'saved_definition',
          remote_control_key: null, detected_at: null, scan_id: null},
      ];
    }

    function recordingFixture(t, hoursAgo, service, seconds, state, endReason, partial) {
      const startedAt = t - hoursAgo * 3600000;
      return {
        id: uuid(), session_id: uuid(), service_id: service.id,
        service_name: service.name, physical_channel: service.physical_channel,
        input_kind: service.input_kind, state, started_at: iso(startedAt),
        ended_at: iso(startedAt + seconds * 1000), duration_limit_seconds: RECORDING_LIMIT,
        elapsed_seconds: seconds, remaining_seconds: 0, end_reason: endReason, partial,
        size_bytes: Math.round(seconds * 2_000_000), artifact_id: partial ? null : uuid(),
        playback_available: !partial, download_available: !partial,
      };
    }

    function reset() {
      base = Date.now();
      started = performance.now();
      const t = now();
      const first = has('first_run');
      const lastScanId = uuid();
      const live = first ? [] : LIVE_SERVICES.map(item => liveService(item, t - 26 * 3600000, lastScanId));
      world = {
        services: live.concat(fileServices()),
        scans: new Map(),
        sessions: new Map(),
        recordings: [],
        activeSession: null,
        activeScan: null,
        activeRecording: null,
        requests: new Map(),
      };
      if (!first) {
        world.recordings = [
          recordingFixture(t, 2, live[0], 300, 'completed', 'deadline', false),
          recordingFixture(t, 5, live[2], 95, 'completed', 'requested', false),
          recordingFixture(t, 27, live[3], 42, 'failed', 'storage_full', true),
          recordingFixture(t, 50, live[5], 130, 'interrupted', 'server_restart', true),
          recordingFixture(t, 52, fileServices()[0], 8, 'completed', 'eof', false),
        ];
      }
    }

    function remember(requestId, kind) {
      // Same request_id returns the same resource, as docs/api.md describes.
      return world.requests.get(`${kind}:${requestId}`) || null;
    }

    // ---- state progression (computed on read, like polling a backend) ----

    function advanceSession(session) {
      if (!session || ['completed', 'failed', 'interrupted'].includes(session.state)) return;
      const t = now();
      const age = (t - Date.parse(session.started_at)) / 1000;
      const live = session.input_kind === 'live';
      if (session.state === 'stopping') {
        if (t >= session._stopAt) {
          session.state = 'completed';
          session.stage = 'complete';
          session.ended_at = iso(t);
          session.end_reason = session.end_reason || 'requested';
          if (live) session.restore = 'verified';
          if (world.activeSession === session.id) world.activeSession = null;
        }
        return;
      }
      const remaining = (Date.parse(session.deadline_at) - t) / 1000;
      if (remaining <= 0) {
        session.state = 'stopping';
        session.end_reason = 'deadline';
        session._stopAt = t + 1000 * speed;
        return;
      }
      if (session.state === 'starting' && age >= session._readyAfter) {
        session.state = 'running';
        session.stage = 'transport';
      }
      const h = session.health;
      if (session.state === 'running') {
        h.signal = 'ok';
        h.ts = 'ok';
        if (h.cas !== 'not_required') h.cas = has('cas_fail') ? 'failed' : 'ok';
        if (h.cas === 'failed') h.hls = 'blocked';
        else if (age >= session._readyAfter + 1.5 * speed) h.hls = has('transcode_fail') ? 'failed' : 'ok';
        session.bytes_received = Math.round(Math.max(0, age - session._readyAfter) * 2_000_000);
      }
      session.remaining_seconds = Math.max(0, Math.round(remaining));
    }

    function advanceScan(scan) {
      if (!scan || ['completed', 'failed', 'interrupted'].includes(scan.state)) return;
      const t = now();
      if (scan.state === 'stopping') {
        if (t >= scan._stopAt) finishScan(scan, 'completed', 'requested');
        return;
      }
      const elapsed = (t - Date.parse(scan.started_at)) / 1000;
      const done = Math.min(scan.channels.length, Math.floor(elapsed / SCAN_SECONDS_PER_CHANNEL));
      scan.state = 'running';
      while (scan.results.length < done) {
        const channel = scan.channels[scan.results.length];
        if (has('scan_fail') && scan.results.length >= Math.min(3, scan.channels.length - 1)) {
          finishScan(scan, 'failed', 'worker_failed');
          return;
        }
        const found = has('scan_none') ? [] : LIVE_SERVICES.filter(s => s.channel === channel);
        const weak = !found.length && channel % 7 === 0 && !has('scan_none');
        scan.results.push({
          physical_channel: channel,
          stage: found.length ? 'ts_si' : (weak ? 'signal' : 'none'),
          services: found.map(item => liveService(item, t, scan.id)),
        });
      }
      scan.done_channels = scan.results.length;
      scan.current_channel = scan.channels[scan.results.length] || null;
      scan.found_services = scan.results.reduce((n, r) => n + r.services.length, 0);
      if (scan.results.length === scan.channels.length) finishScan(scan, 'completed', 'eof');
      else if ((t - Date.parse(scan.started_at)) / 1000 >= scan.duration_seconds) {
        finishScan(scan, 'completed', 'deadline');
      }
    }

    function finishScan(scan, state, reason) {
      const t = now();
      scan.state = state;
      scan.end_reason = reason;
      scan.ended_at = iso(t);
      scan.current_channel = null;
      scan.restore = 'verified';
      const found = scan.results.flatMap(r => r.services);
      // Provisional rule: only a scan that covered its whole range replaces the
      // saved list. Stopped, empty or failed scans keep the previous list.
      scan.saved = state === 'completed' && reason === 'eof' && found.length > 0;
      if (scan.saved) {
        world.services = found.concat(world.services.filter(s => s.input_kind !== 'live'));
      }
      if (world.activeScan === scan.id) world.activeScan = null;
    }

    function advanceRecording(rec) {
      if (!rec || rec.state !== 'running') return;
      const t = now();
      const session = world.sessions.get(rec.session_id);
      advanceSession(session);
      const elapsed = (t - Date.parse(rec.started_at)) / 1000;
      rec.elapsed_seconds = Math.min(RECORDING_LIMIT, Math.floor(elapsed));
      rec.remaining_seconds = Math.max(0, RECORDING_LIMIT - rec.elapsed_seconds);
      rec.size_bytes = rec.elapsed_seconds * 2_000_000;
      if (has('recording_partial') && elapsed >= 40) {
        endRecording(rec, 'failed', 'storage_full', true);
      } else if (elapsed >= RECORDING_LIMIT) {
        rec.elapsed_seconds = RECORDING_LIMIT;
        endRecording(rec, 'completed', 'deadline', false);
      } else if (session && session.state !== 'running') {
        endRecording(rec, 'failed', 'source_ended', true);
      }
    }

    function endRecording(rec, state, reason, partial) {
      rec.state = state;
      rec.end_reason = reason;
      rec.partial = partial;
      rec.ended_at = iso(now());
      rec.remaining_seconds = 0;
      rec.artifact_id = partial ? null : uuid();
      rec.playback_available = !partial;
      rec.download_available = !partial;
      if (world.activeRecording === rec.id) world.activeRecording = null;
    }

    function tick() {
      world.recordings.forEach(advanceRecording);
      world.sessions.forEach(advanceSession);
      world.scans.forEach(advanceScan);
    }

    function publicSession(session) {
      const result = copy(session);
      for (const key of Object.keys(result)) if (key.startsWith('_')) delete result[key];
      return result;
    }

    function blocked() {
      if (has('restore_unknown')) return fail(409, 'restore_unverified');
      if (has('storage_low')) return fail(409, 'storage_full');
      return null;
    }

    reset();

    return {
      mode: 'mock',
      scenarios: SCENARIOS,
      get scenario() { return scenario; },
      get speed() { return speed; },
      setScenario(name) {
        if (!SCENARIOS.some(item => item.id === name)) return;
        scenario = name;
        reset();
      },
      setSpeed(value) {
        const t = now();
        speed = value;
        base = t;
        started = performance.now();
      },
      playerWillFail() {
        return has('player_fail');
      },

      bootstrap() {
        return call(0.2, () => ({
          csrf_token: 'mock-token-not-a-secret', mode: 'mock',
          live_available: true, hls_available: true, recording_available: true,
        }));
      },

      // Provisional: one call that tells a reloaded tab what is active and
      // whether a new receiver start is blocked.
      status() {
        return call(0.15, () => {
          tick();
          return {
            active_session_id: world.activeSession,
            active_scan_id: world.activeScan,
            active_recording_id: world.activeRecording,
            restore: has('restore_unknown') ? 'unknown' : 'verified',
            storage: {ok: !has('storage_low'), free_bytes: has('storage_low') ? 64 * 1024 * 1024 : 48 * 1024 ** 3},
            server_time: iso(now()),
          };
        });
      },

      diagnostics(inputKind) {
        return call(1.2, () => {
          const synthetic = inputKind === 'synthetic';
          const live = inputKind === 'live';
          const checks = {
            storage: {status: has('storage_low') ? 'failed' : 'ok', code: 'free_space'},
            demo_source: {status: 'ok', code: 'synthetic_ts'},
            ffmpeg: {status: 'ok', code: 'generator_and_future_hls'},
            board: {status: live ? 'ok' : 'not_required', code: 'no_device_io'},
            native_receiver: {status: live ? 'ok' : 'not_checked', code: 'separate_runtime_probe'},
            card: {status: synthetic ? 'not_required' : (has('cas_fail') ? 'failed' : 'ok'), code: 'no_card_io'},
            cas: {status: synthetic ? 'not_required' : 'ok', code: 'external_cas_not_configured'},
          };
          if (has('first_run') && live) checks.board.status = 'not_checked';
          return {input_kind: inputKind, checks, starts_receiver: false};
        });
      },

      services() {
        return call(0.3, () => copy(world.services));
      },

      startScan(body) {
        return call(0.4, () => {
          tick();
          const known = remember(body.request_id, 'scan');
          if (known) return copy(world.scans.get(known));
          const stop = blocked();
          if (stop) return stop;
          if (world.activeRecording) return fail(409, 'recording_active');
          if (world.activeSession) return fail(409, 'session_busy');
          if (world.activeScan) return fail(409, 'scan_active');
          const t = now();
          const scan = {
            id: uuid(), request_id: body.request_id, state: 'starting',
            channels: body.channels.slice(), duration_seconds: Math.min(SCAN_LIMIT, body.duration_seconds || SCAN_LIMIT),
            started_at: iso(t), deadline_at: iso(t + SCAN_LIMIT * 1000), ended_at: null,
            end_reason: null, restore: 'pending', done_channels: 0, current_channel: body.channels[0],
            found_services: 0, results: [], saved: false,
          };
          world.scans.set(scan.id, scan);
          world.requests.set(`scan:${body.request_id}`, scan.id);
          world.activeScan = scan.id;
          return copy(scan);
        });
      },
      getScan(id) {
        return call(0.15, () => {
          tick();
          const scan = world.scans.get(id);
          return scan ? copy(scan) : fail(404, 'scan_not_found');
        });
      },
      stopScan(id) {
        return call(0.3, () => {
          tick();
          const scan = world.scans.get(id);
          if (!scan) return fail(404, 'scan_not_found');
          if (scan.state === 'running' || scan.state === 'starting') {
            scan.state = 'stopping';
            scan._stopAt = now() + 800 * speed;
          }
          return copy(scan);
        });
      },

      startSession(body) {
        return call(0.4, () => {
          tick();
          const known = remember(body.request_id, 'session');
          if (known) return publicSession(world.sessions.get(known));
          const service = world.services.find(s => s.id === body.service_ref);
          if (!service) return fail(404, 'service_not_found');
          const stop = blocked();
          if (stop) return stop;
          if (world.activeRecording) return fail(409, 'recording_active');
          if (world.activeScan) return fail(409, 'scan_active');
          if (world.activeSession) return fail(409, 'session_busy');
          const t = now();
          // Only the first session of the scenario has been running for a while,
          // so stopping and tuning again gives a full 600 s, as the UI advises.
          const offset = has('short_session') && !world.sessions.size ? 350 : 0;
          const startedAt = t - offset * 1000;
          const live = service.input_kind === 'live';
          const session = {
            id: uuid(), request_id: body.request_id, input_kind: service.input_kind,
            source_id: live ? 'board' : service.id, service_ref: service.id, // provisional
            service_name: service.name, physical_channel: service.physical_channel, // provisional
            service_id: service.service_id, state: 'starting', stage: 'input',
            duration_seconds: SESSION_LIMIT, started_at: iso(startedAt),
            deadline_at: iso(startedAt + SESSION_LIMIT * 1000), ended_at: null, end_reason: null,
            error_stage: null, restore: live ? 'pending' : 'not_required', partial: false,
            bytes_received: 0, artifact_id: null, remaining_seconds: SESSION_LIMIT - offset,
            health: { // provisional
              signal: live ? 'waiting' : 'not_required', ts: 'waiting',
              cas: service.input_kind === 'synthetic' ? 'not_required' : 'waiting', hls: 'waiting',
            },
            _readyAfter: offset + 1.2 * speed, _stopAt: 0,
          };
          world.sessions.set(session.id, session);
          world.requests.set(`session:${body.request_id}`, session.id);
          world.activeSession = session.id;
          return publicSession(session);
        });
      },
      getSession(id) {
        return call(0.15, () => {
          tick();
          const session = world.sessions.get(id);
          return session ? publicSession(session) : fail(404, 'session_not_found');
        });
      },
      stopSession(id) {
        return call(0.3, () => {
          tick();
          const session = world.sessions.get(id);
          if (!session) return fail(404, 'session_not_found');
          if (session.state === 'starting' || session.state === 'running') {
            const recording = world.recordings.find(r => r.id === world.activeRecording);
            if (recording && recording.session_id === session.id) {
              endRecording(recording, 'failed', 'source_ended', true);
            }
            session.state = 'stopping';
            session.stage = 'cleanup';
            session.end_reason = 'requested';
            session._stopAt = now() + 800 * speed;
          }
          return publicSession(session);
        });
      },

      startRecording(body) {
        return call(0.4, () => {
          tick();
          const known = remember(body.request_id, 'recording');
          if (known) return copy(world.recordings.find(r => r.id === known));
          const session = world.sessions.get(body.session_id);
          if (!session) return fail(404, 'session_not_found');
          if (world.activeRecording) return fail(409, 'recording_active');
          if (has('storage_low')) return fail(409, 'storage_full');
          if (session.state !== 'running') return fail(409, 'session_not_running');
          if (session.remaining_seconds < RECORDING_LIMIT + STOP_GRACE) {
            return fail(409, 'recording_time_insufficient');
          }
          const t = now();
          const rec = {
            id: uuid(), session_id: session.id, service_id: session.service_ref,
            service_name: session.service_name, physical_channel: session.physical_channel,
            input_kind: session.input_kind, state: 'running', started_at: iso(t), ended_at: null,
            duration_limit_seconds: RECORDING_LIMIT, elapsed_seconds: 0,
            remaining_seconds: RECORDING_LIMIT, end_reason: null, partial: false, size_bytes: 0,
            artifact_id: null, playback_available: false, download_available: false,
          };
          world.recordings.unshift(rec);
          world.requests.set(`recording:${body.request_id}`, rec.id);
          world.activeRecording = rec.id;
          return copy(rec);
        });
      },
      getRecording(id) {
        return call(0.15, () => {
          tick();
          const rec = world.recordings.find(r => r.id === id);
          return rec ? copy(rec) : fail(404, 'recording_not_found');
        });
      },
      stopRecording(id) {
        return call(0.3, () => {
          tick();
          const rec = world.recordings.find(r => r.id === id);
          if (!rec) return fail(404, 'recording_not_found');
          if (rec.state === 'running') endRecording(rec, 'completed', 'requested', false);
          return copy(rec);
        });
      },
      recordings() {
        return call(0.3, () => {
          tick();
          return copy(world.recordings);
        });
      },
    };
  }

  window.SdrMock = {createMockBackend, SCENARIOS};
})();
