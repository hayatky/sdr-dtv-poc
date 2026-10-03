// SPDX-License-Identifier: GPL-3.0-or-later
// WebUI screens (#27/#28). The bundled Vue is the runtime-only build and the CSP
// forbids eval, so the screens use render functions instead of templates.
// Station names and other server data are always passed as text children.
(function () {
  'use strict';

  const {createApp, h, reactive, nextTick} = Vue;

  const params = new URLSearchParams(location.search);
  const SPEEDS = [1, 10, 30];
  const requestedSpeed = Number(params.get('speed'));
  const api = SdrApi.create({
    mode: 'mock',
    scenario: params.get('demo') || 'normal',
    speed: SPEEDS.includes(requestedSpeed) ? requestedSpeed : 1,
  });

  const RECORDING_LIMIT = 300;
  const STOP_GRACE = 5;
  const TERMINAL = ['completed', 'failed', 'interrupted'];
  const TABS = [
    {id: 'scan', label: '接続・スキャン'},
    {id: 'watch', label: '視聴'},
    {id: 'recordings', label: '録画'},
  ];

  const INPUT_KINDS = {
    live: {short: '実機ライブ', long: '実機ライブ（SDRボードで受信）', mark: '●'},
    saved_ts: {short: '保存TS', long: '保存TS（保存したファイルを再生）', mark: '■'},
    synthetic: {short: '模擬入力', long: '模擬入力（合成テスト信号）', mark: '◆'},
  };

  const DIAG_ITEMS = [
    {key: 'storage', label: '保存先の空き容量'},
    {key: 'demo_source', label: '合成テスト信号のファイル', only: 'synthetic'},
    {key: 'ffmpeg', label: '映像の変換（FFmpeg）'},
    {key: 'board', label: '受信ボード'},
    {key: 'native_receiver', label: '受信処理のプログラム'},
    {key: 'card', label: 'B-CASカード'},
    {key: 'cas', label: 'カードを使う復号ツール'},
  ];

  const DIAG_STATUS = {
    ok: {label: '使用できます', tone: 'ok', mark: '✓'},
    missing: {label: '見つかりません', tone: 'danger', mark: '✕'},
    failed: {label: '問題があります', tone: 'danger', mark: '✕'},
    not_checked: {label: '未確認', tone: 'warn', mark: '?'},
    not_required: {label: 'この入力では不要', tone: 'muted', mark: '–'},
  };

  const DIAG_HINTS = {
    storage: '保存先の空き容量を増やしてから、もう一度確認してください。',
    demo_source: 'READMEの手順で合成テスト信号のファイルを作成してください。',
    ffmpeg: 'FFmpegを導入してから、もう一度確認してください。',
    board: 'この項目は自動では確認していません。受信を始めたときの状態表示で確認します。',
    native_receiver: 'この項目は自動では確認していません。受信処理の導入手順を確認してください。',
    card: 'カードの差し込みと向きを確認してから、もう一度確認してください。',
    cas: '復号ツールの設定を確認してから、もう一度確認してください。',
  };

  const ERRORS = {
    network_error: ['サーバーと通信できません', '自動的に再接続を試みます。続く場合はサーバーが起動しているか確認してください。'],
    restore_unverified: ['受信機の設定を元に戻せたか確認できていません', '受信機の状態を確認して復旧の記録を残すまで、新しい受信とスキャンは開始できません。'],
    storage_full: ['保存先の空き容量が足りません', '空き容量を増やしてから、接続の確認をやり直してください。'],
    session_busy: ['ほかの受信が動いています', '先に受信を停止してから操作してください。'],
    scan_active: ['スキャン中です', 'スキャンが終わるのを待つか、中止してから操作してください。'],
    recording_active: ['録画中です', '録画を停止してから操作してください。'],
    recording_time_insufficient: ['受信の残り時間が足りません', '5分の録画には、受信の残り時間が5分5秒以上必要です。受信を停止して選局し直すと録画できます。'],
    session_not_running: ['まだ受信が始まっていません', '映像の準備ができてから録画を開始してください。'],
    stop_timeout: ['前の受信を停止できませんでした', '状態が更新されるまで待ってから、もう一度局を選んでください。'],
    live_not_implemented: ['実機での受信はまだ使えません', '模擬入力か保存TSを選んでください。'],
    source_missing: ['入力ファイルが見つかりません', '接続の確認で入力ファイルの状態を確認してください。'],
    source_not_registered: ['入力ファイルが登録されていません', '管理者が保存TSの設定を確認してください。'],
    database_error: ['サーバーの記録に失敗しました', '時間をおいてから、もう一度お試しください。'],
    session_history_limit: ['受信の履歴が上限に達しました', '管理者が保存データを整理するまで、新しい受信は開始できません。'],
    feature_not_implemented: ['この機能はまだ使えません', '後続の実装を待ってください。'],
  };

  const END_REASONS = {
    eof: 'ファイルの終わりで停止',
    requested: '手動で停止',
    deadline: '時間の上限で自動停止',
    startup_timeout: '開始を待つ時間の上限',
    worker_failed: '受信処理の異常',
    storage_full: '空き容量の不足',
    database_error: '記録の失敗',
    output_limit: 'ファイルサイズの上限',
    server_shutdown: 'サーバーの停止',
    server_restart: 'サーバーの再起動',
    source_ended: '受信が先に終了',
  };

  const SCAN_STAGES = {
    none: '信号なし',
    signal: '信号のみ検出（番組情報は未確認）',
    tmcc: '受信情報（TMCC）まで確認',
    ts_si: '番組情報まで確認',
    synthetic_definition: '合成テスト信号の定義',
    saved_definition: '保存TSの登録',
  };

  const STEP_STATUS = {
    waiting: {label: '待機中', tone: 'muted', mark: '…'},
    ok: {label: '正常', tone: 'ok', mark: '✓'},
    failed: {label: '失敗', tone: 'danger', mark: '✕'},
    blocked: {label: '前の段階で停止', tone: 'warn', mark: '!'},
    not_required: {label: '不要', tone: 'muted', mark: '–'},
    synthetic: {label: '合成表示', tone: 'info', mark: '◆'},
  };

  const SCAN_PRESETS = [
    {id: 'all', label: 'UHFの全範囲（13〜52ch）', from: 13, to: 52},
    {id: 'low', label: '低い側（13〜32ch）', from: 13, to: 32},
    {id: 'high', label: '高い側（33〜52ch）', from: 33, to: 52},
    {id: 'custom', label: '範囲を指定する'},
  ];

  // ---------- small helpers ----------

  function el(tag, props, ...kids) {
    const children = kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false && k !== '');
    return h(tag, props || null, children);
  }
  function clock(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }
  function spoken(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    if (!m) return `${r}秒`;
    return r ? `${m}分${r}秒` : `${m}分`;
  }
  const dateFormat = new Intl.DateTimeFormat('ja-JP', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  const timeFormat = new Intl.DateTimeFormat('ja-JP', {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  function when(iso) {
    return iso ? dateFormat.format(new Date(iso)) : '—';
  }
  function bytes(n) {
    if (!n) return '0 MB';
    if (n < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
    return `${(n / 1e9).toFixed(2)} GB`;
  }
  function active(item) {
    return Boolean(item) && !TERMINAL.includes(item.state);
  }
  function stationName(name) {
    return name || '局名未取得';
  }
  function errorText(code) {
    if (ERRORS[code]) return ERRORS[code];
    if (code && code.endsWith('_not_found')) return ['対象が見つかりません', '一覧を更新してから、もう一度お試しください。'];
    return ['操作できませんでした', 'もう一度お試しください。'];
  }
  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  // ---------- state ----------

  const initialTab = TABS.some(t => `#${t.id}` === location.hash) ? location.hash.slice(1) : 'scan';

  const state = reactive({
    tab: initialTab,
    conn: {status: 'loading', lastOkAt: null},
    system: null,
    announce: '',
    diag: {inputKind: 'live', phase: 'idle', result: null, checkedAt: null, error: null},
    scanForm: {preset: 'all', from: 13, to: 52},
    scan: {current: null, pending: false, error: null},
    services: {items: [], loaded: false},
    watch: {targetRef: null, session: null, action: null, error: null, confirmStop: false},
    player: {phase: 'none', sessionId: null},
    rec: {current: null, last: null, pending: false, error: null},
    recordings: {items: [], loaded: false, playing: null, notice: null},
  });

  const refs = {};
  const narrowQuery = window.matchMedia('(max-width: 899px)');
  const view = reactive({narrow: narrowQuery.matches});
  narrowQuery.addEventListener('change', event => { view.narrow = event.matches; });

  function say(text) {
    // Clearing first makes screen readers repeat identical messages.
    state.announce = '';
    nextTick(() => { state.announce = text; });
  }

  function setTab(id, focusHeading) {
    state.tab = id;
    history.replaceState(null, '', `${location.search}#${id}`);
    if (id === 'recordings') loadRecordings();
    if (focusHeading) nextTick(() => refs[`heading-${id}`] && refs[`heading-${id}`].focus());
  }

  function onTabKey(event) {
    const order = TABS.map(t => t.id);
    let index = order.indexOf(state.tab);
    if (event.key === 'ArrowRight') index = (index + 1) % order.length;
    else if (event.key === 'ArrowLeft') index = (index + order.length - 1) % order.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = order.length - 1;
    else return;
    event.preventDefault();
    setTab(order[index]);
    nextTick(() => refs[`tab-${order[index]}`] && refs[`tab-${order[index]}`].focus());
  }

  // ---------- derived state ----------

  const offline = () => state.conn.status === 'offline';
  const restoreBlocked = () => state.system && ['unknown', 'failed'].includes(state.system.restore);
  const storageLow = () => state.system && state.system.storage && !state.system.storage.ok;
  const recording = () => state.rec.current && state.rec.current.state === 'running';
  const session = () => state.watch.session;
  const sessionActive = () => active(session());
  const scanning = () => active(state.scan.current);

  function serviceByRef(ref) {
    return state.services.items.find(s => s.id === ref) || null;
  }

  // Why an operation cannot start now. The API rejects the same cases; the
  // screen disables the control first so that the reason is visible.
  function blockReason(kind) {
    if (offline()) return {text: 'サーバーと通信できないため操作できません。再接続を待ってください。'};
    if (restoreBlocked()) return {text: '受信機の設定を元に戻せたか確認できていないため、新しい受信を開始できません。'};
    if (storageLow()) return {text: '保存先の空き容量が足りないため開始できません。空き容量を増やしてください。'};
    if (kind === 'scan') {
      if (recording()) return {text: '録画中はスキャンできません。先に視聴タブで録画を停止してください。', go: 'watch'};
      if (sessionActive() || state.watch.action) return {text: '視聴中はスキャンできません。先に受信を停止してください。', stop: true};
    }
    if (kind === 'tune') {
      if (recording()) return {text: '録画中は局を切り替えられません。先に録画を停止してください。'};
      if (scanning()) return {text: 'スキャン中は局を選べません。スキャンが終わるのを待つか、中止してください。', go: 'scan'};
    }
    return null;
  }

  function recordBlock() {
    const s = session();
    if (offline()) return 'サーバーと通信できないため操作できません。';
    if (storageLow()) return '保存先の空き容量が足りないため録画できません。';
    if (!s || s.state !== 'running') return '局を選んで受信が始まると録画できます。';
    if (s.remaining_seconds < RECORDING_LIMIT + STOP_GRACE) {
      return `受信の残り時間（${spoken(s.remaining_seconds)}）が足りないため、5分の録画を開始できません。受信を停止して選局し直すと録画できます。`;
    }
    return null;
  }

  function scanRange() {
    const f = state.scanForm;
    const preset = SCAN_PRESETS.find(p => p.id === f.preset);
    const from = preset.id === 'custom' ? Number(f.from) : preset.from;
    const to = preset.id === 'custom' ? Number(f.to) : preset.to;
    if (!Number.isInteger(from) || !Number.isInteger(to) || from < 13 || to > 52 || from > to) {
      return {ok: false, message: '13〜52の整数で、開始は終了以下のチャンネルを指定してください。'};
    }
    const channels = [];
    for (let ch = from; ch <= to; ch += 1) channels.push(ch);
    return {ok: true, channels, from, to};
  }

  // ---------- server synchronisation ----------

  let timer = null;
  let polling = false;
  let pageHidden = false;

  function markOnline() {
    state.conn.status = 'ok';
    state.conn.lastOkAt = new Date().toISOString();
  }
  function setOffline() {
    if (state.conn.status !== 'offline') say('サーバーと通信できなくなりました。自動的に再接続します。');
    state.conn.status = 'offline';
  }
  function noteError(error) {
    if (error.status === 0) setOffline();
  }

  function schedule() {
    clearTimeout(timer);
    if (pageHidden) return;
    const busy = sessionActive() || scanning() || recording() || state.watch.action;
    timer = setTimeout(refresh, offline() || !busy ? 5000 : 1000);
  }

  async function refresh() {
    if (polling || pageHidden) return;
    polling = true;
    clearTimeout(timer);
    try {
      const wasOffline = offline();
      const status = await api.status();
      markOnline();
      state.system = status;
      if (wasOffline) {
        say('サーバーとの通信が戻りました。');
        if (!state.services.loaded) loadServices();
        loadRecordings();
      }
      await syncSession(status);
      await syncScan(status);
      await syncRecording(status);
    } catch (error) {
      noteError(error);
    } finally {
      polling = false;
      schedule();
    }
  }

  async function syncSession(status) {
    const w = state.watch;
    if (w.action) return; // the running action owns the session state
    if (active(w.session)) {
      const id = w.session.id;
      const next = await api.getSession(id);
      // Ignore late answers about a session that is no longer shown.
      if (w.session && w.session.id === id && !w.action) applySession(next);
    } else if (status.active_session_id && (!w.session || w.session.id !== status.active_session_id)) {
      // Reload or another tab: show the running session; never start a new one.
      const next = await api.getSession(status.active_session_id);
      w.session = next;
      w.targetRef = next.service_ref;
      say(`受信中の${stationName(next.service_name)}を表示します。`);
      applySession(next);
    }
  }

  function applySession(next) {
    const w = state.watch;
    const before = w.session;
    w.session = next;
    if (next.state === 'running' && next.health && next.health.hls === 'ok' && state.player.sessionId !== next.id) {
      startPlayer(next.id);
    }
    if (!active(next)) {
      resetPlayer();
      w.confirmStop = false;
      if (active(before)) say(`受信を停止しました（${END_REASONS[next.end_reason] || '終了'}）。`);
    }
  }

  async function syncScan(status) {
    const sc = state.scan;
    if (active(sc.current)) {
      const id = sc.current.id;
      const next = await api.getScan(id);
      if (sc.current && sc.current.id === id) {
        sc.current = next;
        if (!active(next)) onScanEnded(next);
      }
    } else if (status.active_scan_id && (!sc.current || sc.current.id !== status.active_scan_id)) {
      sc.current = await api.getScan(status.active_scan_id);
    }
  }

  function onScanEnded(scan) {
    if (scan.saved) loadServices();
    say(scanResult(scan).title);
  }

  async function syncRecording(status) {
    const r = state.rec;
    if (r.current && r.current.state === 'running') {
      const id = r.current.id;
      const next = await api.getRecording(id);
      if (r.current && r.current.id === id) {
        r.current = next;
        if (next.state !== 'running') onRecordingEnded(next);
      }
    } else if (status.active_recording_id && (!r.current || r.current.id !== status.active_recording_id)) {
      r.current = await api.getRecording(status.active_recording_id);
    }
  }

  function onRecordingEnded(rec) {
    state.rec.last = rec;
    say(rec.partial ? '録画が途中で終了しました。' : '録画を保存しました。');
    loadRecordings();
  }

  async function loadServices() {
    try {
      state.services.items = await api.services();
      state.services.loaded = true;
      markOnline();
    } catch (error) {
      noteError(error);
    }
  }

  async function loadRecordings() {
    try {
      state.recordings.items = await api.recordings();
      state.recordings.loaded = true;
      markOnline();
    } catch (error) {
      noteError(error);
    }
  }

  // ---------- player (synthetic display only in #28) ----------

  let playerTimer = null;
  function resetPlayer() {
    clearTimeout(playerTimer);
    state.player = {phase: 'none', sessionId: null};
  }
  function startPlayer(sessionId) {
    clearTimeout(playerTimer);
    state.player = {phase: 'preparing', sessionId};
    playerTimer = setTimeout(() => {
      if (state.player.sessionId !== sessionId) return;
      state.player.phase = api.demo && api.demo.playerWillFail() ? 'failed' : 'showing';
    }, 1000);
  }

  // ---------- user actions ----------

  async function runDiagnostics() {
    const d = state.diag;
    d.phase = 'running';
    d.error = null;
    try {
      d.result = await api.diagnostics(d.inputKind);
      d.checkedAt = new Date().toISOString();
      d.phase = 'done';
      markOnline();
      say('接続の確認が終わりました。');
    } catch (error) {
      d.phase = 'error';
      d.error = error.code;
      noteError(error);
    }
  }

  async function startScan() {
    const range = scanRange();
    const sc = state.scan;
    if (!range.ok || sc.pending) return;
    sc.pending = true;
    sc.error = null;
    try {
      sc.current = await api.startScan(range.channels, api.newRequestId());
      say(`${range.from}chから${range.to}chまでのスキャンを開始しました。`);
    } catch (error) {
      sc.error = error.code;
      noteError(error);
    } finally {
      sc.pending = false;
      refresh();
    }
  }

  async function stopScan() {
    const sc = state.scan;
    if (!sc.current || sc.pending) return;
    sc.pending = true;
    try {
      sc.current = await api.stopScan(sc.current.id);
      say('スキャンを中止しています。');
    } catch (error) {
      sc.error = error.code;
      noteError(error);
    } finally {
      sc.pending = false;
      refresh();
    }
  }

  async function waitStopped(id) {
    const limit = Date.now() + 15000;
    while (Date.now() < limit) {
      const s = await api.getSession(id);
      if (!active(s)) return s;
      await sleep(400);
    }
    throw new SdrApi.ApiError(409, 'stop_timeout');
  }

  async function tune(ref) {
    const w = state.watch;
    if (w.action || blockReason('tune')) return;
    if (active(w.session) && w.session.service_ref === ref) {
      setTab('watch', true);
      return;
    }
    w.error = null;
    w.confirmStop = false;
    w.targetRef = ref;
    resetPlayer();
    setTab('watch', true);
    try {
      if (active(w.session)) {
        w.action = 'switching';
        const old = w.session.id;
        await api.stopSession(old);
        await waitStopped(old);
      }
      w.session = null;
      w.action = 'starting';
      const next = await api.startSession(ref, api.newRequestId());
      if (w.targetRef === ref) w.session = next;
      say(`${stationName(serviceByRef(ref) && serviceByRef(ref).name)}を選局しています。`);
    } catch (error) {
      w.error = error.code;
      noteError(error);
      say(errorText(error.code)[0]);
    } finally {
      w.action = null;
      refresh();
    }
  }

  async function stopReceiving() {
    const w = state.watch;
    if (!active(w.session) || w.action) return;
    if (recording() && !w.confirmStop) {
      w.confirmStop = true;
      nextTick(() => refs.confirmStop && refs.confirmStop.focus());
      return;
    }
    w.confirmStop = false;
    w.action = 'stopping';
    try {
      w.session = await api.stopSession(w.session.id);
      resetPlayer();
      say('受信を停止しています。');
    } catch (error) {
      w.error = error.code;
      noteError(error);
    } finally {
      w.action = null;
      refresh();
    }
  }

  function cancelStop() {
    state.watch.confirmStop = false;
    nextTick(() => refs.stopButton && refs.stopButton.focus());
  }

  async function startRecording() {
    const r = state.rec;
    if (r.pending || recording() || recordBlock()) return;
    r.pending = true;
    r.error = null;
    r.last = null;
    try {
      r.current = await api.startRecording(session().id, api.newRequestId());
      say('録画を開始しました。5分になると自動的に停止します。');
    } catch (error) {
      r.error = error.code;
      noteError(error);
      say(errorText(error.code)[0]);
    } finally {
      r.pending = false;
      refresh();
    }
  }

  async function stopRecording() {
    const r = state.rec;
    if (r.pending || !recording()) return;
    r.pending = true;
    try {
      const next = await api.stopRecording(r.current.id);
      r.current = next;
      if (next.state !== 'running') onRecordingEnded(next);
    } catch (error) {
      r.error = error.code;
      noteError(error);
    } finally {
      r.pending = false;
      refresh();
    }
  }

  function changeScenario(id) {
    api.demo.setScenario(id);
    const url = new URLSearchParams(location.search);
    url.set('demo', id);
    history.replaceState(null, '', `?${url}${location.hash}`);
    resetPlayer();
    Object.assign(state.watch, {targetRef: null, session: null, action: null, error: null, confirmStop: false});
    Object.assign(state.scan, {current: null, pending: false, error: null});
    Object.assign(state.rec, {current: null, last: null, pending: false, error: null});
    Object.assign(state.diag, {phase: 'idle', result: null, checkedAt: null, error: null});
    Object.assign(state.recordings, {playing: null, notice: null});
    state.services.loaded = false;
    state.recordings.loaded = false;
    state.system = null;
    say('デモの状態を切り替えました。');
    start();
  }

  function changeSpeed(value) {
    api.demo.setSpeed(value);
    const url = new URLSearchParams(location.search);
    url.set('speed', String(value));
    history.replaceState(null, '', `?${url}${location.hash}`);
  }

  async function start() {
    try {
      await api.bootstrap();
      markOnline();
    } catch (error) {
      noteError(error);
    }
    await Promise.all([loadServices(), loadRecordings()]);
    await refresh();
  }

  // ---------- shared parts ----------

  function chip(tone, mark, label, extra) {
    return el('span', {class: ['chip', `tone-${tone}`, extra]}, el('span', {class: 'mark', 'aria-hidden': 'true'}, mark), label);
  }
  function inputBadge(kind) {
    const info = INPUT_KINDS[kind];
    if (!info) return null;
    return el('span', {class: ['badge', `input-${kind}`], title: info.long},
      el('span', {'aria-hidden': 'true'}, info.mark), info.short);
  }
  function notice(tone, title, body, actions) {
    return el('div', {class: ['notice', `tone-${tone}`]},
      el('p', {class: 'notice-title'}, title),
      body ? el('p', null, body) : null,
      actions && actions.length ? el('div', {class: 'actions'}, actions) : null);
  }
  function errorNotice(code) {
    if (!code) return null;
    const [title, body] = errorText(code);
    return notice('danger', title, body);
  }
  function button(label, onClick, options) {
    const o = options || {};
    return el('button', {
      type: 'button', class: ['btn', o.kind ? `btn-${o.kind}` : null], disabled: Boolean(o.disabled),
      onClick, 'aria-describedby': o.describedby, ref: o.ref,
    }, label);
  }
  function tech(summary, rows) {
    return el('details', {class: 'tech'},
      el('summary', null, summary),
      el('dl', null, rows.filter(Boolean).map(([k, v]) => [el('dt', null, k), el('dd', null, v === null || v === undefined ? '—' : String(v))])));
  }
  function heading(id, text) {
    return el('h2', {class: 'panel-title', tabindex: '-1', ref: node => { refs[`heading-${id}`] = node; }}, text);
  }
  function blockNotice(reason) {
    if (!reason) return null;
    const actions = [];
    if (reason.stop) actions.push(button('受信を停止する', stopReceiving, {kind: 'secondary'}));
    if (reason.go) actions.push(button(reason.go === 'watch' ? '視聴タブへ移動' : '接続・スキャンタブへ移動', () => setTab(reason.go, true), {kind: 'secondary'}));
    return notice('warn', '今は開始できません', reason.text, actions);
  }

  // ---------- header and global notices ----------

  function summary() {
    const s = session();
    const w = state.watch;
    if (offline()) return [chip('danger', '✕', 'サーバーと通信できません')];
    if (recording()) {
      const r = state.rec.current;
      return [chip('rec', '●', '録画中'), el('span', null, `${stationName(r.service_name)}　残り ${clock(r.remaining_seconds)}`)];
    }
    if (w.action === 'switching' || w.action === 'starting') {
      const target = serviceByRef(w.targetRef);
      return [chip('info', '…', '選局中'), el('span', null, stationName(target && target.name))];
    }
    if (active(s)) {
      return [chip('ok', '▶', s.state === 'stopping' ? '停止中' : '受信中'), el('span', null, stationName(s.service_name)), inputBadge(s.input_kind)];
    }
    if (scanning()) {
      const sc = state.scan.current;
      return [chip('info', '…', 'スキャン中'), el('span', null, `${sc.done_channels} / ${sc.channels.length}ch`)];
    }
    if (state.conn.status === 'loading') return [el('span', null, '読み込んでいます…')];
    return [chip('muted', '–', '受信していません')];
  }

  function renderHeader() {
    return el('header', {class: 'app-header'},
      el('div', {class: 'container header-row'},
        el('p', {class: 'brand'},
          el('span', {class: 'brand-title'}, 'SDR地上波テレビ'),
          el('span', {class: 'demo-tag'}, 'デモ表示')),
        el('p', {class: 'now'}, el('span', {class: 'visually-hidden'}, '現在の状態：'), summary())),
      el('div', {class: 'container'},
        el('div', {class: 'tabs', role: 'tablist', 'aria-label': '画面の切り替え', onKeydown: onTabKey},
          TABS.map(tab => {
            const selected = state.tab === tab.id;
            const dot = tab.id === 'watch' && recording() ? el('span', {class: 'tab-dot', 'aria-label': '（録画中）'}, '●')
              : tab.id === 'scan' && scanning() ? el('span', {class: 'tab-dot info', 'aria-label': '（スキャン中）'}, '…') : null;
            return el('button', {
              type: 'button', role: 'tab', id: `tab-${tab.id}`, class: 'tab',
              'aria-selected': selected ? 'true' : 'false', 'aria-controls': `panel-${tab.id}`,
              tabindex: selected ? 0 : -1, onClick: () => setTab(tab.id),
              ref: node => { refs[`tab-${tab.id}`] = node; },
            }, tab.label, dot);
          }))));
  }

  function renderDemoBanner() {
    const demo = api.demo;
    return el('section', {class: 'demo-banner', 'aria-labelledby': 'demo-title'},
      el('p', {class: 'demo-title', id: 'demo-title'}, '◆ 表示確認用のデモです'),
      el('p', null, '画面に出る局・映像・録画はすべて架空の模擬データです。受信・録画・ファイルの保存は行っていません。'),
      demo ? el('details', {class: 'demo-controls'},
        el('summary', null, 'デモの状態を切り替える'),
        el('div', {class: 'demo-grid'},
          el('label', {for: 'demo-scenario'}, '再現する状態'),
          el('select', {id: 'demo-scenario', value: demo.scenario, onChange: e => changeScenario(e.target.value)},
            demo.scenarios.map(item => el('option', {value: item.id}, item.label))),
          el('label', {for: 'demo-speed'}, '模擬の時間の速さ'),
          el('select', {id: 'demo-speed', value: String(demo.speed), onChange: e => changeSpeed(Number(e.target.value))},
            SPEEDS.map(v => el('option', {value: String(v)}, v === 1 ? '実時間' : `${v}倍速`)))),
        el('p', {class: 'hint'}, '状態を切り替えると、模擬データを最初の状態に戻します。速さは残り時間やスキャンの進み方だけに影響します。'))
        : null);
  }

  function renderGlobalNotices() {
    const list = [];
    if (offline()) {
      list.push(notice('danger', 'サーバーと通信できません',
        `自動的に再接続を試みています。${state.conn.lastOkAt
          ? `表示は最後に通信できたとき（${timeFormat.format(new Date(state.conn.lastOkAt))}）の内容です。`
          : 'まだサーバーから情報を取得できていません。'}録画中だった場合も、録画はサーバー側の期限で停止します。`,
        [button('今すぐ再接続する', refresh, {kind: 'secondary'})]));
    }
    if (restoreBlocked()) {
      list.push(notice('danger', '受信機の設定を元に戻せたか確認できていません',
        '前回の受信の後、受信機の設定を元に戻せたかを確認できませんでした。安全のため、確認が済むまで新しい受信とスキャンは開始できません。受信機の状態を確認し、復旧の記録を残してください。'));
    }
    if (storageLow()) {
      list.push(notice('warn', '保存先の空き容量が足りません',
        `空き容量は約${bytes(state.system.storage.free_bytes)}です。受信と録画を始める前に、空き容量を増やしてください。`));
    }
    return list.length ? el('div', {class: 'global-notices'}, list) : null;
  }

  // ---------- tab 1: connection and scan ----------

  function renderDiagnostics() {
    const d = state.diag;
    const result = d.result;
    const items = result ? DIAG_ITEMS.filter(item => !item.only || item.only === result.input_kind) : [];
    return el('section', {class: 'panel', 'aria-labelledby': 'diag-title'},
      el('h3', {id: 'diag-title'}, '1. 接続を確認する'),
      el('p', null, '受信を始める前に、必要な機器とプログラムがそろっているかを確認します。この確認では受信を開始しません。'),
      el('fieldset', {class: 'choice'},
        el('legend', null, '確認する入力元'),
        Object.entries(INPUT_KINDS).map(([kind, info]) => el('label', {class: 'radio'},
          el('input', {type: 'radio', name: 'diag-input', value: kind, checked: d.inputKind === kind,
            onChange: () => { Object.assign(d, {inputKind: kind, result: null, phase: 'idle', error: null}); }}),
          info.long))),
      el('div', {class: 'actions'},
        button(d.phase === 'running' ? '確認しています…' : '接続を確認する', runDiagnostics, {kind: 'primary', disabled: d.phase === 'running' || offline()})),
      d.phase === 'error' ? errorNotice(d.error) : null,
      result ? [
        el('p', {class: 'meta'}, `${INPUT_KINDS[result.input_kind].long}の確認結果（${when(d.checkedAt)}時点）`),
        el('ul', {class: 'check-list'}, items.map(item => {
          const check = result.checks[item.key] || {status: 'not_checked'};
          const info = DIAG_STATUS[check.status] || DIAG_STATUS.not_checked;
          return el('li', {class: 'check'},
            el('span', {class: 'check-label'}, item.label),
            chip(info.tone, info.mark, info.label),
            ['missing', 'failed', 'not_checked'].includes(check.status) ? el('p', {class: 'hint'}, DIAG_HINTS[item.key]) : null);
        })),
        tech('技術的な値を表示', [
          ['入力の種類（input_kind）', result.input_kind],
          ['受信を開始したか（starts_receiver）', result.starts_receiver ? 'はい' : 'いいえ'],
          ...items.map(item => [item.label, `${(result.checks[item.key] || {}).status} / ${(result.checks[item.key] || {}).code}`]),
        ]),
      ] : null);
  }

  function scanResult(scan) {
    const found = scan.found_services;
    const kept = '以前に保存した局の一覧はそのまま残しています。';
    if (scan.state === 'failed') {
      return {tone: 'danger', title: 'スキャンを完了できませんでした', body: `理由：${END_REASONS[scan.end_reason] || '不明'}。${kept}受信ボードとアンテナの接続を確認してから、もう一度お試しください。`};
    }
    if (scan.state === 'interrupted') {
      return {tone: 'warn', title: 'サーバーの再起動でスキャンが中断されました', body: `${kept}もう一度スキャンしてください。`};
    }
    if (scan.end_reason === 'requested') {
      return {tone: 'info', title: 'スキャンを中止しました', body: `${kept}中止までに見つかった${found}局は保存していません。`};
    }
    if (scan.end_reason === 'deadline') {
      return {tone: 'warn', title: '時間内にすべてのチャンネルを調べられませんでした', body: `スキャンは最大3分です。範囲を狭めて、もう一度お試しください。${kept}`};
    }
    if (!found) {
      return {tone: 'warn', title: '局が見つかりませんでした', body: `${kept}受信ボードとアンテナの接続や範囲を確認してから、もう一度お試しください。`};
    }
    return {tone: 'ok', title: `スキャンが終わりました。${found}局を保存しました。`, body: '視聴タブで局を選ぶと受信を始めます。'};
  }

  function renderScan() {
    const f = state.scanForm;
    const sc = state.scan;
    const range = scanRange();
    const current = sc.current;
    const running = active(current);
    const reason = running ? null : blockReason('scan');
    return el('section', {class: 'panel', 'aria-labelledby': 'scan-title'},
      el('h3', {id: 'scan-title'}, '2. 局を探す（スキャン）'),
      el('p', null, '実機（SDRボード）で、指定した範囲のチャンネルから受信できる局を探します。最大3分で自動的に終了します。'),
      blockNotice(reason),
      el('fieldset', {class: 'choice', disabled: running},
        el('legend', null, '調べる範囲'),
        SCAN_PRESETS.map(p => el('label', {class: 'radio'},
          el('input', {type: 'radio', name: 'scan-preset', value: p.id, checked: f.preset === p.id, onChange: () => { f.preset = p.id; }}),
          p.label)),
        f.preset === 'custom' ? el('div', {class: 'range'},
          el('label', {for: 'scan-from'}, '開始チャンネル'),
          el('input', {id: 'scan-from', type: 'number', inputmode: 'numeric', min: 13, max: 52, value: f.from,
            'aria-describedby': 'scan-range-help', onInput: e => { f.from = e.target.value; }}),
          el('label', {for: 'scan-to'}, '終了チャンネル'),
          el('input', {id: 'scan-to', type: 'number', inputmode: 'numeric', min: 13, max: 52, value: f.to,
            'aria-describedby': 'scan-range-help', onInput: e => { f.to = e.target.value; }})) : null,
        el('p', {id: 'scan-range-help', class: ['hint', range.ok ? null : 'invalid']},
          range.ok ? `${range.from}〜${range.to}ch（${range.channels.length}チャンネル）を調べます。` : range.message)),
      el('div', {class: 'actions'},
        running
          ? button(current.state === 'stopping' ? '中止しています…' : 'スキャンを中止する', stopScan, {kind: 'danger', disabled: sc.pending || current.state === 'stopping'})
          : button(sc.pending ? '開始しています…' : 'スキャンを開始する', startScan, {kind: 'primary', disabled: sc.pending || !range.ok || Boolean(reason)})),
      errorNotice(sc.error),
      current ? renderScanProgress(current) : null);
  }

  function renderScanProgress(scan) {
    const total = scan.channels.length;
    const found = scan.results.filter(r => r.services.length);
    const running = active(scan);
    const result = running ? null : scanResult(scan);
    return el('div', {class: 'scan-progress'},
      running ? el('div', null,
        el('label', {for: 'scan-bar', class: 'progress-label'},
          scan.state === 'stopping' ? 'スキャンを中止しています…' : `${scan.current_channel || '—'}chを調べています（${scan.done_channels} / ${total}）`),
        el('progress', {id: 'scan-bar', max: total, value: scan.done_channels}),
        el('p', {class: 'meta'}, `見つかった局：${scan.found_services}局`))
        : notice(result.tone, result.title, result.body,
          scan.saved ? [button('視聴タブで局を選ぶ', () => setTab('watch', true), {kind: 'secondary'})] : []),
      found.length ? el('ul', {class: 'found-list', 'aria-label': 'このスキャンで見つかった局'},
        found.map(r => el('li', null,
          el('span', {class: 'ch'}, `物理${r.physical_channel}ch`),
          el('span', null, r.services.map(s => stationName(s.name)).join('、'))))) : null,
      tech('チャンネルごとの結果を表示', scan.results.map(r => [
        `物理${r.physical_channel}ch`, `${SCAN_STAGES[r.stage] || r.stage}${r.services.length ? `（${r.services.length}サービス）` : ''}`,
      ]).concat([['スキャンID', scan.id], ['状態（state / end_reason）', `${scan.state} / ${scan.end_reason || '—'}`]])));
  }

  function renderSavedStations() {
    const live = state.services.items.filter(s => s.input_kind === 'live');
    const lastDetected = live.reduce((t, s) => (s.detected_at && s.detected_at > t ? s.detected_at : t), '');
    const reason = blockReason('tune');
    return el('section', {class: 'panel', 'aria-labelledby': 'saved-title'},
      el('h3', {id: 'saved-title'}, '3. 保存された局'),
      el('p', null, '過去のスキャンで見つかった局です。今受信できるかどうかは、選局したときの状態表示で確認します。'),
      !state.services.loaded
        ? el('p', {class: 'empty'}, offline() ? 'サーバーと通信できないため、局の一覧を表示できません。' : '局の一覧を読み込んでいます…')
        : !live.length
          ? el('div', {class: 'empty'},
            el('p', {class: 'empty-title'}, 'まだ局が保存されていません'),
            el('p', null, '上の「局を探す」で範囲を選び、スキャンを開始してください。見つかった局がここに表示されます。'))
          : [
            el('p', {class: 'meta'}, `前回の検出：${when(lastDetected)}（スキャン結果。現在の受信状態ではありません）`),
            el('table', {class: 'stations'},
              el('caption', {class: 'visually-hidden'}, '保存された局の一覧'),
              el('thead', null, el('tr', null,
                ['リモコン番号', '局名', '物理チャンネル', '確認できた段階', '操作'].map(t => el('th', {scope: 'col'}, t)))),
              el('tbody', null, live.map(s => el('tr', null,
                el('td', {'data-label': 'リモコン番号'}, s.remote_control_key ? String(s.remote_control_key) : '—'),
                el('th', {scope: 'row', 'data-label': '局名', class: ['name', s.name ? null : 'unknown']}, stationName(s.name)),
                el('td', {'data-label': '物理チャンネル'}, `${s.physical_channel}ch`),
                el('td', {'data-label': '確認できた段階'}, SCAN_STAGES[s.detection_stage] || s.detection_stage),
                el('td', {'data-label': '操作'},
                  button('視聴する', () => tune(s.id), {kind: 'secondary', disabled: Boolean(reason) || Boolean(state.watch.action)})))))),
            tech('技術的な値を表示', live.map(s => [stationName(s.name), `service ID ${s.service_id}／物理${s.physical_channel}ch／${s.detection_stage}`])),
          ]);
  }

  function renderScanTab() {
    return [heading('scan', '接続・スキャン'), renderDiagnostics(), renderScan(), renderSavedStations()];
  }

  // ---------- tab 2: watch ----------

  function watchPhase() {
    const w = state.watch;
    const s = w.session;
    if (w.action === 'switching') return 'switching';
    if (w.action === 'starting') return 'starting';
    if (w.action === 'stopping') return 'stopping';
    if (!s) return w.error ? 'error' : 'idle';
    if (s.state === 'starting') return 'starting';
    if (s.state === 'stopping') return 'stopping';
    if (!active(s)) return 'ended';
    if (s.health.cas === 'failed') return 'cas_failed';
    if (s.health.hls === 'failed') return 'hls_failed';
    if (s.health.hls !== 'ok') return 'hls_waiting';
    if (state.player.phase === 'failed') return 'player_failed';
    if (state.player.phase === 'showing') return 'showing';
    return 'player_preparing';
  }

  const PLAYER_TEXT = {
    idle: ['映像はここに表示します', '局の一覧から選ぶと受信を始めます。'],
    error: ['受信を開始できませんでした', '下の案内を確認してください。'],
    switching: ['前の受信を停止しています…', '停止が終わると、選んだ局の受信を始めます。'],
    starting: ['受信を開始しています…', '電波と番組データを確認しています。'],
    hls_waiting: ['映像を準備しています…', '受信したデータをブラウザーで再生できる形式に変換しています。'],
    player_preparing: ['再生を準備しています…', ''],
    stopping: ['受信を停止しています…', ''],
    ended: ['受信を停止しました', ''],
    cas_failed: ['映像を表示できません', 'カードによる復号に失敗しました。'],
    hls_failed: ['映像を表示できません', '映像の変換に失敗しました。'],
    player_failed: ['映像を表示できません', 'このブラウザーで再生できませんでした。'],
  };

  function renderPlayer(phase) {
    const s = session();
    if (phase === 'showing') {
      return el('div', {class: 'player showing', role: 'img', 'aria-label': '合成表示のテストパターン。映像と音声は再生していません。'},
        el('div', {class: 'pattern', 'aria-hidden': 'true'}),
        el('div', {class: 'player-caption', 'aria-hidden': 'true'},
          el('strong', null, '合成表示'),
          el('span', null, '映像・音声は再生していません')));
    }
    const [title, body] = PLAYER_TEXT[phase] || PLAYER_TEXT.idle;
    const failed = ['cas_failed', 'hls_failed', 'player_failed', 'error'].includes(phase);
    const ended = phase === 'ended' && s ? `理由：${END_REASONS[s.end_reason] || '不明'}` : body;
    return el('div', {class: ['player', failed ? 'failed' : null]},
      el('div', {class: 'player-message', role: 'status'},
        el('strong', null, title), ended ? el('span', null, ended) : null));
  }

  function steps(phase) {
    const s = session();
    if (!s || !s.health) return null;
    const player = phase === 'showing' ? 'synthetic' : phase === 'player_failed' ? 'failed'
      : ['cas_failed', 'hls_failed'].includes(phase) ? 'blocked' : 'waiting';
    const items = [
      ['電波の受信', s.health.signal],
      ['番組データ', s.health.ts],
      ['カードによる復号', s.health.cas],
      ['映像の変換', s.health.hls],
      ['ブラウザーでの表示', active(s) ? player : 'waiting'],
    ];
    return el('ol', {class: 'steps', 'aria-label': '受信から表示までの状態'},
      items.map(([label, status]) => {
        const info = STEP_STATUS[status] || STEP_STATUS.waiting;
        return el('li', {class: ['step', `tone-${info.tone}`]},
          el('span', {class: 'step-label'}, label),
          chip(info.tone, info.mark, info.label));
      }));
  }

  function phaseNotice(phase) {
    const w = state.watch;
    if (phase === 'error') return errorNotice(w.error);
    if (phase === 'cas_failed') {
      return notice('danger', 'カードによる復号に失敗しました',
        '電波と番組データは受信できていますが、映像を表示できません。カードの差し込みと向きを確認し、受信を停止してから選局し直してください。録画は受信したままのデータとして保存できます。');
    }
    if (phase === 'hls_failed') {
      return notice('danger', '映像の変換に失敗しました',
        '受信は続いています。受信を停止して選局し直してください。続く場合は「接続・スキャン」で映像の変換の状態を確認してください。');
    }
    if (phase === 'player_failed') {
      return notice('danger', 'このブラウザーで再生できませんでした',
        '受信と映像の変換は動いています。「再生をやり直す」を押してください。続く場合は別のブラウザーでお試しください。',
        [button('再生をやり直す', () => startPlayer(session().id), {kind: 'secondary'})]);
    }
    if (phase === 'ended') {
      const s = session();
      if (s && (s.partial || s.state !== 'completed')) {
        return notice('warn', '受信が途中で終了しました', `理由：${END_REASONS[s.end_reason] || '不明'}。もう一度局を選ぶと受信をやり直します。`);
      }
    }
    return w.error && phase !== 'error' ? errorNotice(w.error) : null;
  }

  function renderRecordingBox() {
    const r = state.rec;
    const rec = r.current && r.current.state === 'running' ? r.current : null;
    const block = rec ? null : recordBlock();
    return el('section', {class: ['record-box', rec ? 'is-recording' : null], 'aria-labelledby': 'rec-title'},
      el('h3', {id: 'rec-title'}, '録画'),
      rec ? [
        el('p', {class: 'rec-now'}, chip('rec', '●', '録画中'),
          el('span', {class: 'rec-time'}, `${clock(rec.elapsed_seconds)} / ${clock(RECORDING_LIMIT)}`),
          el('span', null, `残り ${clock(rec.remaining_seconds)}`)),
        el('progress', {max: RECORDING_LIMIT, value: rec.elapsed_seconds, 'aria-label': `録画の経過時間。残り${spoken(rec.remaining_seconds)}`}),
        el('p', {class: 'hint'}, '5分になるとサーバーが自動的に録画を停止します。画面を閉じても録画は期限で停止します。録画中は局の切り替えとスキャンはできません。'),
        el('div', {class: 'actions'}, button(r.pending ? '停止しています…' : '録画を停止する', stopRecording, {kind: 'danger', disabled: r.pending})),
      ] : [
        el('p', {class: 'hint', id: 'rec-help'}, block || '受信中のデータをそのまま最大5分間保存します。5分になると自動的に停止します。'),
        el('div', {class: 'actions'}, button(r.pending ? '開始しています…' : '録画を開始する（最大5分）', startRecording,
          {kind: 'record', disabled: Boolean(block) || r.pending, describedby: 'rec-help'})),
      ],
      errorNotice(r.error),
      !rec && r.last ? (r.last.partial
        ? notice('warn', '録画が途中で終了しました', `理由：${END_REASONS[r.last.end_reason] || '不明'}。途中までの録画は「録画」タブで確認できます。`,
          [button('録画タブで確認する', () => setTab('recordings', true), {kind: 'secondary'})])
        : notice('ok', `録画を保存しました（${clock(r.last.elapsed_seconds)}）`,
          `${r.last.end_reason === 'deadline' ? '5分の上限で自動停止しました' : '録画を停止しました'}。${active(session()) ? '受信は続いています。' : ''}`,
          [button('録画タブで確認する', () => setTab('recordings', true), {kind: 'secondary'})])) : null);
  }

  function renderStationPicker() {
    const reason = blockReason('tune');
    const busy = Boolean(state.watch.action);
    const groups = [
      {kind: 'live', title: '実機で受信する局（前回のスキャン結果）'},
      {kind: 'saved_ts', title: '保存したTSファイル'},
      {kind: 'synthetic', title: '合成テスト信号'},
    ];
    const current = active(session()) ? session().service_ref : null;
    return el('section', {class: 'panel picker', 'aria-labelledby': 'picker-title'},
      el('h3', {id: 'picker-title'}, '局を選ぶ'),
      reason ? el('p', {class: 'hint block-hint', id: 'picker-block'}, reason.text) : null,
      !state.services.loaded ? el('p', {class: 'empty'}, offline() ? 'サーバーと通信できないため、局の一覧を表示できません。' : '読み込んでいます…') : null,
      state.services.loaded ? groups.map(g => {
        const items = state.services.items.filter(s => s.input_kind === g.kind);
        return el('div', {class: 'picker-group'},
          el('h4', null, g.title),
          items.length ? el('ul', {class: 'station-list'}, items.map(s => {
            const selected = current === s.id || (busy && state.watch.targetRef === s.id);
            const label = [stationName(s.name), s.remote_control_key ? `リモコン${s.remote_control_key}` : null,
              s.physical_channel ? `物理${s.physical_channel}ch` : null, INPUT_KINDS[s.input_kind].long,
              current === s.id ? '受信中' : null].filter(Boolean).join('、');
            return el('li', null, el('button', {
              type: 'button', class: ['station', selected ? 'selected' : null, g.kind === 'live' ? null : 'no-remote'],
              'aria-pressed': selected ? 'true' : 'false',
              'aria-label': label,
              disabled: Boolean(reason) || busy, 'aria-describedby': reason ? 'picker-block' : undefined,
              onClick: () => tune(s.id),
            },
            g.kind === 'live' ? el('span', {class: 'station-remote'}, s.remote_control_key ? `リモコン${s.remote_control_key}` : '番号なし') : null,
            el('span', {class: ['station-name', s.name ? null : 'unknown']}, stationName(s.name)),
            el('span', {class: 'station-meta'},
              s.physical_channel ? `物理${s.physical_channel}ch` : null,
              inputBadge(s.input_kind),
              current === s.id ? chip('ok', '▶', '受信中') : null)));
          })) : el('p', {class: 'empty small'}, g.kind === 'live'
            ? ['スキャン結果はまだありません。', button('接続・スキャンへ移動', () => setTab('scan', true), {kind: 'link'})]
            : 'ありません。'));
      }) : null);
  }

  function renderWatchTab() {
    const phase = watchPhase();
    const w = state.watch;
    // While switching, nothing from the previous session may be shown.
    const pending = w.action === 'switching' || w.action === 'starting';
    const s = pending ? null : session();
    const wanted = serviceByRef(w.targetRef);
    const target = pending ? (wanted ? {name: wanted.name, kind: wanted.input_kind} : null)
      : s ? {name: s.service_name, kind: s.input_kind} : null;
    const canStop = active(s) && s.state !== 'stopping' && !w.action;
    return [
      heading('watch', '視聴'),
      el('div', {class: 'watch-grid'},
        // On narrow screens the station list comes first until a station is chosen.
        view.narrow && phase === 'idle' ? renderStationPicker() : null,
        el('div', {class: 'watch-main'},
          el('section', {class: 'panel', 'aria-labelledby': 'now-title'},
            el('div', {class: 'now-title-row'},
              el('h3', {id: 'now-title', class: ['now-title', target && !target.name ? 'unknown' : null]},
                target ? stationName(target.name) : '局が選ばれていません'),
              target ? inputBadge(target.kind) : null),
            renderPlayer(phase),
            phase === 'showing' ? el('p', {class: 'player-note'}, '表示確認用の合成表示です。実際の映像・音声の再生は、APIとの接続（#29）で確認します。') : null,
            phaseNotice(phase),
            s ? steps(phase) : null,
            active(s) && s.state === 'running' ? el('p', {class: 'meta'},
              `受信は残り${clock(s.remaining_seconds)}で自動的に停止します（1回の受信は最大10分）。`) : null,
            s ? el('div', {class: 'actions'},
              w.confirmStop ? el('div', {class: 'confirm', role: 'group', 'aria-label': '受信停止の確認',
                onKeydown: e => { if (e.key === 'Escape') cancelStop(); }},
                el('p', null, '録画中です。受信を停止すると録画も停止します。'),
                button('録画と受信を停止する', stopReceiving, {kind: 'danger', ref: node => { refs.confirmStop = node; }}),
                button('キャンセル', cancelStop, {kind: 'secondary'}))
                : button(phase === 'stopping' ? '停止しています…' : '受信を停止する', stopReceiving,
                  {kind: 'secondary', disabled: !canStop, ref: node => { refs.stopButton = node; }})) : null,
            s ? tech('技術的な値を表示', [
              ['入力元', INPUT_KINDS[s.input_kind] && INPUT_KINDS[s.input_kind].long],
              ['物理チャンネル', s.physical_channel ? `${s.physical_channel}ch` : '—'],
              ['service ID', s.service_id],
              ['受信したデータ量', bytes(s.bytes_received)],
              ['開始時刻', when(s.started_at)],
              ['自動停止の予定時刻', when(s.deadline_at)],
              ['状態（state / stage）', `${s.state} / ${s.stage}`],
              ['終了理由（end_reason）', s.end_reason],
              ['受信機の設定の復元（restore）', s.restore],
              ['セッションID', s.id],
            ]) : null),
          renderRecordingBox()),
        view.narrow && phase === 'idle' ? null : renderStationPicker()),
    ];
  }

  // ---------- tab 3: recordings ----------

  function recStatus(r) {
    if (r.state === 'running') return chip('rec', '●', '録画中');
    if (r.state === 'interrupted') return chip('warn', '!', '中断（途中まで）');
    if (r.partial || r.state === 'failed') return chip('warn', '!', '途中で終了');
    return chip('ok', '✓', '完了');
  }

  function renderPlayback() {
    const id = state.recordings.playing;
    const r = state.recordings.items.find(x => x.id === id);
    if (!r) return null;
    return el('section', {class: 'panel playback', 'aria-labelledby': 'playback-title'},
      el('div', {class: 'now-title-row'},
        el('h3', {id: 'playback-title', tabindex: '-1', ref: node => { refs.playback = node; }}, `録画の再生：${stationName(r.service_name)}`),
        inputBadge(r.input_kind)),
      el('div', {class: 'player showing', role: 'img', 'aria-label': '合成表示のテストパターン。録画は再生していません。'},
        el('div', {class: 'pattern', 'aria-hidden': 'true'}),
        el('div', {class: 'player-caption', 'aria-hidden': 'true'}, el('strong', null, '合成表示'), el('span', null, '録画は再生していません'))),
      el('p', {class: 'player-note'}, '表示確認用の合成表示です。録画の再生は、APIとの接続（#29）で確認します。再生には別に作った再生用のファイルを使い、受信したままのファイルは変更しません。'),
      el('div', {class: 'actions'}, button('再生を閉じる', () => {
        state.recordings.playing = null;
        nextTick(() => refs[`play-${id}`] && refs[`play-${id}`].focus());
      }, {kind: 'secondary'})));
  }

  function renderRecordingsTab() {
    const list = state.recordings;
    return [
      heading('recordings', '録画'),
      el('p', {class: 'lead'}, '録画の一覧です。受信したままのデータ（TSファイル）を保存しています。'),
      renderPlayback(),
      list.notice ? notice('info', list.notice, null, [button('閉じる', () => { list.notice = null; }, {kind: 'secondary'})]) : null,
      !list.loaded
        ? el('p', {class: 'empty'}, offline() ? 'サーバーと通信できないため、録画の一覧を表示できません。' : '読み込んでいます…')
        : !list.items.length
          ? el('div', {class: 'empty'},
            el('p', {class: 'empty-title'}, 'まだ録画がありません'),
            el('p', null, '視聴タブで局を選び、「録画を開始する」を押すと、ここに表示されます。'),
            button('視聴タブへ移動', () => setTab('watch', true), {kind: 'secondary'}))
          : el('ul', {class: 'rec-list'}, list.items.map(r => {
            const reason = r.end_reason === 'deadline' ? '5分の上限で自動停止' : END_REASONS[r.end_reason];
            const partial = r.partial || r.state === 'failed' || r.state === 'interrupted';
            return el('li', {class: 'rec-item'},
              el('div', {class: 'rec-head'},
                el('h3', {class: ['rec-name', r.service_name ? null : 'unknown']}, stationName(r.service_name)),
                recStatus(r)),
              el('p', {class: 'rec-meta'},
                el('span', null, when(r.started_at)),
                el('span', null, `長さ ${clock(r.elapsed_seconds)}`),
                el('span', null, bytes(r.size_bytes)),
                inputBadge(r.input_kind)),
              r.state === 'running' ? el('p', {class: 'hint'}, `録画中です。残り${clock(r.remaining_seconds)}で自動的に停止します。`)
                : el('p', {class: 'hint'}, partial
                  ? `${reason || '不明な理由'}のため途中で終了しました。途中までのデータは再生・ダウンロードの対象外です（仮の動作）。`
                  : `${reason || '終了'}。`),
              el('div', {class: 'actions'},
                button('再生する', () => {
                  list.playing = r.id;
                  nextTick(() => refs.playback && refs.playback.focus());
                }, {kind: 'secondary', disabled: !r.playback_available, ref: node => { refs[`play-${r.id}`] = node; }}),
                button('TSファイルをダウンロード', () => {
                  list.notice = '表示確認用のデモのため、ファイルはダウンロードしません。';
                }, {kind: 'secondary', disabled: !r.download_available})),
              tech('技術的な値を表示', [
                ['録画ID', r.id], ['受信セッションID', r.session_id], ['物理チャンネル', r.physical_channel ? `${r.physical_channel}ch` : '—'],
                ['状態（state / end_reason）', `${r.state} / ${r.end_reason || '—'}`], ['途中終了（partial）', r.partial ? 'はい' : 'いいえ'],
                ['ファイルID（artifact_id）', r.artifact_id], ['終了時刻', when(r.ended_at)],
              ]));
          })),
    ];
  }

  // ---------- root ----------

  const App = {
    setup() {
      start();
      window.addEventListener('hashchange', () => {
        const id = location.hash.slice(1);
        if (TABS.some(t => t.id === id) && id !== state.tab) setTab(id);
      });
      window.addEventListener('pagehide', () => {
        pageHidden = true;
        clearTimeout(timer);
        clearTimeout(playerTimer);
      });
      window.addEventListener('pageshow', event => {
        pageHidden = false;
        if (event.persisted) refresh();
      });
      return () => el('div', {class: 'app'},
        el('a', {class: 'skip', href: '#main', onClick: e => {
          e.preventDefault();
          const main = document.getElementById('main');
          if (main) main.focus();
        }}, '本文へ移動'),
        renderHeader(),
        el('main', {id: 'main', class: 'container', tabindex: '-1'},
          renderDemoBanner(),
          renderGlobalNotices(),
          TABS.map(tab => el('div', {
            id: `panel-${tab.id}`, role: 'tabpanel', 'aria-labelledby': `tab-${tab.id}`,
            hidden: state.tab !== tab.id, class: 'tabpanel',
          }, state.tab !== tab.id ? null
            : tab.id === 'scan' ? renderScanTab() : tab.id === 'watch' ? renderWatchTab() : renderRecordingsTab()))),
        el('footer', {class: 'container footer'},
          el('p', null, 'SDR DTV PoC — 実験的なOpen Source PoCです。対応する機器・環境は検証済みの範囲に限られます。'),
          el('p', null, el('a', {href: '/openapi.json'}, 'APIの仕様（OpenAPI）'), '　',
            el('a', {href: '/static/vendor/manifest.json'}, '同梱しているライブラリの一覧'))),
        el('p', {class: 'visually-hidden', role: 'status', 'aria-live': 'polite'}, state.announce));
    },
  };

  createApp(App).mount('#app');
})();
