// SPDX-License-Identifier: GPL-3.0-or-later
// Same-origin transport and explicit projections for the existing screen model.
(function () {
  'use strict';
  class ApiError extends Error {
    constructor(status, code) { super(code); this.status = status; this.code = code; }
  }
  const active = item => item && ['starting', 'running', 'stopping'].includes(item.state);
  const remaining = item => Math.max(0, (Date.parse(item.deadline_at) - Date.now()) / 1000);
  const serviceFields = item => ({...item, service_ref: item.service?.id ?? null,
    service_name: item.service?.name ?? null, physical_channel: item.service?.physical_channel ?? null,
    service_id: item.selected_service_id});
  const sessionView = item => ({...serviceFields(item), remaining_seconds: remaining(item),
    // These are display projections, not additional API fields or RF/CAS proof.
    health: {signal: item.input_kind === 'live' ? 'not_checked' : 'not_required',
      ts: item.bytes_received > 0 ? 'ok' : 'waiting',
      cas: item.hls?.error_stage === 'cas' ? 'failed' : item.input_kind === 'synthetic' ? 'not_required' : 'not_checked',
      hls: ['ready', 'completed'].includes(item.hls?.state) ? 'ok' : item.hls?.state === 'failed' ? 'failed' : 'waiting'}});
  const recordingView = item => {
    const available = item.state === 'completed' && !item.partial && item.file_available && Boolean(item.download_url);
    return {...serviceFields(item), size_bytes: item.bytes_written,
      remaining_seconds: active(item) ? remaining(item) : 0,
      playback_available: available, download_available: available};
  };
  const scanView = item => ({...item, channels: item.results.map(r => r.physical_channel),
    done_channels: item.completed_channels,
    found_services: item.results.reduce((n, r) => n + r.service_ids.length, 0),
    saved: item.results.some(r => r.service_ids.length),
    results: item.results.map(r => ({...r, stage: r.state, services: r.service_ids.map(id => ({id}))}))});

  function http() {
    let token = null;
    let generation = 0;
    const controllers = new Set();
    // Only an explicit retry of the identical action reuses an uncertain start.
    // sessionStorage preserves it across a reload without sharing it across tabs.
    let uncertain = {};
    try { uncertain = JSON.parse(sessionStorage.getItem('sdr-uncertain') || '{}'); } catch (_) { /* optional storage */ }
    function save() {
      try { sessionStorage.setItem('sdr-uncertain', JSON.stringify(uncertain)); } catch (_) { /* optional storage */ }
    }
    async function request(path, body) {
      const controller = new AbortController();
      const own = generation;
      controllers.add(controller);
      const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
          headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': token || ''},
          ...(body === undefined ? {} : {body: JSON.stringify(body)})});
        const value = await response.json();
        if (own !== generation) throw new ApiError(0, 'cancelled');
        if (!response.ok) {
          if (response.status === 403) token = null;
          throw new ApiError(response.status, value.code || 'http_error');
        }
        return value;
      } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError(0, own !== generation ? 'cancelled' : 'network_error');
      } finally {
        clearTimeout(timeout);
        controllers.delete(controller);
      }
    }
    async function bootstrap() {
      const value = await request('/api/bootstrap'); token = value.csrf_token; return value;
    }
    async function post(path, body = {}) {
      // Never bootstrap and replay a mutation implicitly after a restart.
      if (!token) throw new ApiError(403, 'csrf_refresh_required');
      return request(path, body);
    }
    async function start(path, body) {
      const {request_id, ...input} = body;
      const key = path + JSON.stringify(input);
      const id = uncertain[key] || request_id;
      uncertain[key] = id; save();
      try {
        const value = await post(path, {...input, request_id: id});
        delete uncertain[key]; save(); return value;
      } catch (error) {
        error.requestId = id;
        if (error.status >= 400 && error.status < 500 && error.status !== 403) { delete uncertain[key]; save(); }
        throw error;
      }
    }
    return {
      mode: 'http', demo: null, newRequestId: () => crypto.randomUUID(), bootstrap,
      cancel() { generation += 1; for (const controller of controllers) controller.abort(); },
      diagnostics: kind => request(`/api/diagnostics?input_kind=${encodeURIComponent(kind)}`),
      services: async () => (await request('/api/services')).map(s => ({...s, remote_control_key: s.remote_control_key_id})),
      recordings: async () => (await request('/api/recordings')).map(recordingView),
      async status() {
        const [sessions, scans, recordings] = await Promise.all([
          request('/api/sessions'), request('/api/scans'), request('/api/recordings')]);
        const latest = items => items.slice().sort((a, b) => b.started_at.localeCompare(a.started_at))[0] || null;
        const current = items => latest(items.filter(active)) || latest(items);
        const resolved = new Set([...sessions, ...scans, ...recordings].map(item => item.request_id));
        for (const [key, id] of Object.entries(uncertain)) { if (resolved.has(id)) delete uncertain[key]; }
        save();
        const s = current(sessions), sc = current(scans), r = current(recordings);
        return {session: s && sessionView(s), scan: sc && scanView(sc), recording: r && recordingView(r),
          recordings: recordings.map(recordingView),
          observedRequestIds: [...resolved],
          stoppedIds: [...sessions, ...scans, ...recordings].filter(item => !active(item)).map(item => item.id),
          // Global restoration/storage gates are not exposed by these endpoints.
          restore: null, storage: null};
      },
      startScan: async (channels, request_id) => scanView(await start('/api/scans', {request_id, input_kind: 'synthetic', channels})),
      getScan: async id => scanView(await request(`/api/scans/${encodeURIComponent(id)}`)),
      stopScan: async id => scanView(await post(`/api/scans/${encodeURIComponent(id)}/stop`)),
      startSession: async (service_key, request_id) => sessionView(await start('/api/sessions', {request_id, service_key, duration_seconds: 600, enable_hls: true})),
      getSession: async id => sessionView(await request(`/api/sessions/${encodeURIComponent(id)}`)),
      stopSession: async id => sessionView(await post(`/api/sessions/${encodeURIComponent(id)}/stop`)),
      startRecording: async (session_id, request_id) => recordingView(await start('/api/recordings', {request_id, session_id, duration_seconds: 300})),
      getRecording: async id => recordingView(await request(`/api/recordings/${encodeURIComponent(id)}`)),
      stopRecording: async id => recordingView(await post(`/api/recordings/${encodeURIComponent(id)}/stop`)),
      startPlayback: id => post(`/api/recordings/${encodeURIComponent(id)}/playback`),
      getPlayback: id => request(`/api/recordings/${encodeURIComponent(id)}/playback`),
    };
  }
  function create(options) {
    if (options.mode === 'http') return http();
    if (options.mode !== 'mock') throw new Error('Unknown mode');
    const backend = window.SdrMock.createMockBackend(options);
    let generation = 0;
    const run = (name, ...args) => backend[name](...args).catch(error => {
      throw new ApiError(error.status ?? 0, error.code || 'unknown_error');
    });
    return {mode: 'mock', demo: backend, cancel() { generation += 1; }, newRequestId: () => crypto.randomUUID(),
      bootstrap: () => run('bootstrap'), status: () => run('status'),
      diagnostics: kind => run('diagnostics', kind), services: () => run('services'), recordings: () => run('recordings'),
      startScan: (channels, request_id) => run('startScan', {channels, request_id}),
      getScan: id => run('getScan', id), stopScan: id => run('stopScan', id),
      async startSession(service_ref, request_id) {
        const own = generation;
        const status = await run('status');
        if (own !== generation) throw new ApiError(0, 'cancelled');
        if (status.active_session_id) {
          await run('stopSession', status.active_session_id);
          const deadline = Date.now() + 15000;
          while (active(await run('getSession', status.active_session_id))) {
            if (own !== generation) throw new ApiError(0, 'cancelled');
            if (Date.now() > deadline) throw new ApiError(409, 'stop_timeout');
            await new Promise(resolve => setTimeout(resolve, 200));
          }
        }
        if (own !== generation) throw new ApiError(0, 'cancelled');
        return run('startSession', {service_ref, request_id});
      },
      getSession: id => run('getSession', id), stopSession: id => run('stopSession', id),
      startRecording: (session_id, request_id) => run('startRecording', {session_id, request_id}),
      getRecording: id => run('getRecording', id), stopRecording: id => run('stopRecording', id)};
  }
  window.SdrApi = {create, ApiError};
})();
