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
    mode: params.get('mode') === 'mock' ? 'mock' : 'http',
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
    synthetic: {short: '合成TS', long: '合成TS（自作のテスト映像・音声）', mark: '◆'},
  };

  const DIAG_ITEMS = [
    {key: 'storage', label: '保存先の空き容量'},
    {key: 'demo_source', label: '合成テスト信号のファイル', only: 'synthetic'},
    {key: 'ffmpeg', label: '映像の変換（FFmpeg）'},
    {key: 'service_information', label: '局名の取得', only: 'live'},
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
    service_information: '局名を読み取るTSDuckが必要です。実機用イメージを更新してから、もう一度確認してください。',
    board: 'この項目は自動では確認していません。受信を始めたときの状態表示で確認します。',
    native_receiver: 'この項目は自動では確認していません。受信処理の導入手順を確認してください。',
    card: 'カードの差し込みと向きを確認してから、もう一度確認してください。',
    cas: '復号ツールの設定を確認してから、もう一度確認してください。',
  };

  const ERRORS = {
    network_error: ['サーバーと通信できません', '自動的に再接続を試みます。続く場合はサーバーが起動しているか確認してください。'],
    restore_unverified: ['受信機の設定を元に戻せたか確認できていません', '受信機の状態を確認し、画面に表示された復旧操作を行うまで、新しい受信とスキャンは開始できません。'],
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
    recording_delete_failed: ['録画ファイルの削除が完了しませんでした', '保存先の権限や状態を確認し、削除を再試行してください。'],
    recording_deleted: ['この録画は削除されています', '録画一覧を更新してください。'],
    session_history_limit: ['受信の履歴が上限に達しました', '管理者が保存データを整理するまで、新しい受信は開始できません。'],
    feature_not_implemented: ['この機能はまだ使えません', '後続の実装を待ってください。'],
  };

  Object.assign(ERRORS, {
    csrf_refresh_required: ['接続情報を更新しました', 'サーバーが再起動した可能性があります。現在の状態を確認してから、必要な操作をもう一度行ってください。自動では開始しません。'],
    forbidden: ['操作が拒否されました', '接続情報を再取得します。状態を確認してから操作してください。'],
    scan_busy: ERRORS.scan_active, recording_busy: ERRORS.recording_active,
    insufficient_session_time: ['録画に必要な入力の残り時間が足りません', '5分録画には停止猶予を含む305秒の残量が必要です。受信を停止して選局し直してください。'],
    device_busy: ['別の処理が受信機を使用しています', '使用中の処理を確認してください。自動では停止しません。'],
    board_unreachable: ['受信機に接続できません', 'USBを差し直して10秒待ってから、もう一度お試しください。同じエラーなら、Ubuntu側の復旧手順（docs/recovery.md）を確認してください。'],
    settings_changed: ['受信機の設定を確認できません', '保存していた基準値と現在の設定が一致しません。受信を開始せず、機器と設定を確認してから再試行してください。'],
    recovery_evidence_missing: ['復旧の確認記録を作成できません', '復旧結果を確認できる記録がありません。受信を開始せず、管理者が復旧状態を確認してください。'],
    recovery_failed: ['受信機の復旧に失敗しました', 'USBの接続とホスト側の準備を確認してから、もう一度お試しください。'],
    playback_busy: ['別の録画を再生する準備中です', '準備が終わるまでお待ちください。'],
    recording_incomplete: ['録画が完了していません', '途中終了した録画は再生できません。'],
    recording_file_missing: ['録画ファイルがありません', '管理者が保存先を確認してください。'],
    recording_unavailable: ['この録画は利用できません', '未完了・途中終了・ファイルの欠損を確認してください。'],
    recording_output_limit: ['録画の保存上限に達しました', '管理者が保存先を確認してください。'],
  });

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
    searching_tmcc: '放送方式を調べています', tmcc_detected: '放送方式を検出',
    unsupported_tmcc: '検出した放送方式には未対応', unstable_tmcc: '放送方式を安定して確認できませんでした',
    no_service: '放送方式を検出しましたが局情報を取得できませんでした',
    detected: 'TSの番組情報を検出', not_detected: '今回未検出', not_run: '未実行',
    none: '信号なし',
    signal: '信号のみ検出（番組情報は未確認）',
    tmcc: '受信情報（TMCC）まで確認',
    ts_si: '番組情報まで確認',
    synthetic_definition: '合成テスト信号の定義',
    saved_definition: '保存TSの登録',
  };

  const STEP_STATUS = {
    not_checked: {label: '未確認', tone: 'warn', mark: '?'},
    waiting: {label: '待機中', tone: 'muted', mark: '…'},
    ok: {label: '正常', tone: 'ok', mark: '✓'},
    failed: {label: '失敗', tone: 'danger', mark: '✕'},
    blocked: {label: '前の段階で停止', tone: 'warn', mark: '!'},
    not_required: {label: '不要', tone: 'muted', mark: '–'},
    synthetic: {label: '合成表示', tone: 'info', mark: '◆'},
  };

  const SCAN_PRESETS = [
    {id: 'synthetic', label: '合成TSのプリセット'},
    {id: 'live', label: '実機の登録済みチャンネル'},
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
    return ['操作できませんでした', `理由：${code || '不明'}。状態を確認してから操作してください。`];
  }

  // ---------- state ----------

  const initialTab = TABS.some(t => `#${t.id}` === location.hash) ? location.hash.slice(1) : 'scan';

  const state = reactive({
    tab: initialTab, bootstrap: null, suspended: false, mutating: false, mediaEpoch: 0,
    playback: null, playbackPlayer: {phase: 'none'}, globalError: null,
    conn: {status: 'loading', lastOkAt: null},
    system: null, recovery: null, recoveryChecked: false, recoveryAction: false, recoveryError: null, recoveryNotice: false,
    announce: '',
    diag: {inputKind: 'synthetic', phase: 'idle', result: null, checkedAt: null, error: null},
    scanForm: {preset: api.demo ? 'all' : 'synthetic', from: 13, to: 52, inputSelected: false},
    scan: {current: null, pending: false, error: null},
    services: {items: [], loaded: false},
    watch: {targetRef: null, session: null, action: null, error: null, confirmStop: false},
    player: {phase: 'none', sessionId: null},
    rec: {current: null, last: null, pending: false, error: null},
    recordings: {items: [], loaded: false, playing: null, notice: null, deleteId: null},
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

  const offline = () => state.conn.status !== 'ok';
  const recoveryVisible = () => !api.demo && state.recovery &&
    (Boolean(state.recovery.required) || ['running', 'failed'].includes(state.recovery.state) || state.recoveryAction);
  const recoveryRunning = () => Boolean(state.recoveryAction) || state.recovery?.state === 'running';
  const recoveryGate = () => !api.demo && Boolean(state.recovery?.required);
  const restoreBlocked = () => {
    // The recovery endpoint is authoritative for the live device gate. Session
    // and scan history may retain an old restore value after a successful run.
    if (!api.demo && state.recovery) return recoveryGate() || recoveryRunning();
    return ['unknown', 'failed'].includes(state.system?.restore) || ['unknown', 'failed'].includes(session()?.restore) || ['unknown', 'failed'].includes(state.scan.current?.restore) || [state.watch.error, state.scan.error, state.rec.error].includes('restore_unverified');
  };
  const storageLow = () => state.diag.result?.checks?.storage?.status === 'failed' || (state.system?.storage && !state.system.storage.ok);
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
    if (state.mutating) return {text: '操作の結果を確認しています。'};
    if (offline()) return {text: 'サーバーと通信できないため操作できません。再接続を待ってください。'};
    if (restoreBlocked()) return {text: '受信機の設定を元に戻せたか確認できていないため、新しい受信を開始できません。'};
    if (storageLow()) return {text: '保存先の空き容量が足りないため開始できません。空き容量を増やしてください。'};
    if (kind === 'scan') {
      if (!api.demo && !state.bootstrap?.scan_available) return {text: 'スキャン機能は利用できません。'};
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
    if (state.mutating) return '操作の結果を確認しています。';
    if (!api.demo && !state.bootstrap?.recording_available) return '録画機能は利用できません。';
    if (restoreBlocked()) return '受信機の復元状態を確認してください。';
    if (offline()) return 'サーバーと通信できないため操作できません。';
    if (storageLow()) return '保存先の空き容量が足りないため録画できません。';
    if (!s || s.state !== 'running') return '局を選んで受信が始まると録画できます。';
    if (api.demo && s.remaining_seconds < RECORDING_LIMIT + STOP_GRACE) {
      return `受信の残り時間（${spoken(s.remaining_seconds)}）が足りないため、5分の録画を開始できません。受信を停止して選局し直すと録画できます。`;
    }
    return null;
  }

  function selectInput(kind) {
    Object.assign(state.diag, {inputKind: kind, result: null, phase: 'idle', error: null});
    state.scanForm.inputSelected = true;
    if (!api.demo) state.scanForm.preset = kind === 'live' ? 'all' : 'synthetic';
  }

  function scanPresets() {
    if (api.demo) return SCAN_PRESETS.filter(p => !['synthetic', 'live'].includes(p.id));
    if (state.diag.inputKind === 'saved_ts') return [];
    return SCAN_PRESETS.filter(p => state.diag.inputKind === 'live' ? p.id !== 'synthetic' : p.id !== 'live');
  }

  function scanRange() {
    const f = state.scanForm;
    if (!api.demo && state.diag.inputKind === 'saved_ts') {
      return {ok: false, message: '保存TSからのスキャンには現在対応していません。入力元を実機ライブまたは合成TSへ変更してください。'};
    }
    if (!scanPresets().some(p => p.id === f.preset)) return {ok: false, message: '入力元と調べる範囲を選び直してください。'};
    if (f.preset === 'synthetic' || f.preset === 'live') {
      const channels = state.bootstrap?.scan_presets?.[f.preset] || [];
      return {ok: channels.length > 0, channels, from: channels[0], to: channels.at(-1), message: state.bootstrap ? 'この入力元のチャンネルが登録されていません。' : '接続情報を取得しています。'};
    }
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

  // One read loop and one explicit mutation at a time. A generation fences every
  // completion, including finally blocks, across actions and page lifecycle.
  let timer = null, polling = false, pageHidden = false, epoch = 0;
  const valid = own => own === epoch && !pageHidden;
  function markOnline() {
    state.conn.status = 'ok'; state.conn.lastOkAt = new Date().toISOString();
  }
  function noteError(error) {
    if (error.code === 'cancelled') return;
    if (error.status === 0 || error.status >= 500) {
      state.conn.status = 'offline'; state.suspended = true;
    }
    if (error.status === 403) {
      state.bootstrap = null;
      state.globalError = 'csrf_refresh_required';
      resetPlayer();
    }
  }
  function schedule() {
    clearTimeout(timer);
    if (pageHidden || (state.mutating && !state.recoveryAction)) return;
    const busy = sessionActive() || scanning() || recording() || state.recordings.playing;
    timer = setTimeout(refresh, offline() || !busy ? 5000 : 1000);
  }
  function resetPlayer() {
    state.mediaEpoch += 1;
    state.player = {phase: 'none', sessionId: null};
  }
  function applySession(next) {
    const before = state.watch.session;
    if (before?.id !== next?.id || (active(before) && !active(next))) resetPlayer();
    state.watch.session = next;
    state.watch.targetRef = next?.service_ref || null;
    if (!active(next)) state.watch.confirmStop = false;
    if (api.demo && next?.health?.hls === 'ok') state.player = {
      phase: api.demo.playerWillFail() ? 'failed' : 'showing', sessionId: next.id};
  }
  function clearRecoveryUi() {
    state.recoveryError = null;
    state.recoveryNotice = true;
    if (state.globalError === 'restore_unverified') state.globalError = null;
    for (const target of [state.watch, state.scan, state.rec]) {
      if (target.error === 'restore_unverified') {
        target.error = null;
        target.recovery = null;
      }
    }
    state.recoveryChecked = false;
    say('受信機の復旧を確認しました。新しい受信は自動で開始していません。');
  }
  async function refresh() {
    if (polling || pageHidden || (state.mutating && !state.recoveryAction)) return;
    polling = true; clearTimeout(timer);
    const own = epoch;
    try {
      const boot = await api.bootstrap();
      const [status, services, recordings] = await Promise.all([
        api.status(), api.services(), api.demo ? api.recordings() : Promise.resolve(null)]);
      if (!valid(own)) return;
      state.bootstrap = boot;
      if (!api.demo && !state.scanForm.inputSelected) selectInput(boot.live_available ? 'live' : 'synthetic');
      state.system = status;
      const previousRecovery = state.recovery;
      state.recovery = status.recovery || null;
      if (state.recovery?.error_code) state.recoveryError = state.recovery.error_code;
      else if (state.recovery?.state === 'completed' && !state.recovery.required) state.recoveryError = null;
      else if (!state.recovery?.required && state.recovery?.state !== 'failed') state.recoveryError = null;
      if (state.recovery?.state === 'completed' && !state.recovery.required && previousRecovery?.state !== 'completed') clearRecoveryUi();
      state.services = {items: services, loaded: true};
      state.recordings.items = recordings || status.recordings;
      state.recordings.loaded = true;
      if (api.demo) {
        const ids = [status.active_session_id || state.watch.session?.id,
          status.active_scan_id || state.scan.current?.id, status.active_recording_id || state.rec.current?.id];
        const values = await Promise.all(ids.map((id, i) => id ? [api.getSession, api.getScan, api.getRecording][i](id) : null));
        if (!valid(own)) return;
        [status.session, status.scan, status.recording] = values;
      }
      const previousScan = state.scan.current, previousRec = state.rec.current;
      applySession(status.session);
      state.scan.current = status.scan;
      state.rec.current = status.recording;
      for (const target of [state.watch, state.scan, state.rec]) {
        const recovery = target.recovery;
        if (target.error === 'network_error' && recovery &&
            (recovery.requestId ? status.observedRequestIds?.includes(recovery.requestId) :
              status.stoppedIds?.includes(recovery.stoppedId))) {
          target.error = null; target.recovery = null;
          say("サーバーから操作の結果を確認しました。");
        }
      }
      if (active(previousScan) && !active(status.scan) && status.scan) say(scanResult(status.scan).title);
      if (active(previousRec) && !active(status.recording) && status.recording) onRecordingEnded(status.recording);
      // A recovered page only reads the selected playback job; never POSTs it.
      markOnline();
      const id = state.recordings.playing;
      if (!api.demo && id) {
        try {
          const playback = await api.getPlayback(id);
          if (!valid(own) || state.recordings.playing !== id) return;
          state.playback = playback;
        } catch (error) {
          if (!valid(own)) return;
          if (error.status === 404) {
            state.recordings.error = error.code; state.recordings.playing = null;
          } else throw error;
        }
      }
      state.suspended = false;
    } catch (error) { if (valid(own)) noteError(error); }
    finally { polling = false; schedule(); }
  }
  async function loadRecordings() { await refresh(); }
  function onRecordingEnded(rec) {
    state.rec.last = rec;
    say(rec.state === 'completed' && !rec.partial ? '録画を保存しました。' : '録画が途中で終了しました。');
  }
  async function mutate(target, action, apply, stoppedId = null) {
    if (state.mutating || pageHidden) return;
    const own = ++epoch;
    state.mutating = true; target.pending = true; target.error = null; target.recovery = null;
    clearTimeout(timer); api.cancel();
    if (state.diag.phase === 'running') state.diag.phase = 'idle';
    try {
      const result = await action();
      if (valid(own)) { apply(result); state.globalError = null; }
    } catch (error) {
      if (valid(own)) {
        target.error = error.code;
        if (error.code === 'network_error') target.recovery = {requestId: error.requestId, stoppedId};
        noteError(error); say(errorText(error.code)[0]);
      }
    } finally {
      if (own === epoch) {
        state.mutating = false; target.pending = false; state.watch.action = null;
        // A previous cancelled read may still be settling. Its finally schedules
        // the sole loop; this read starts immediately only if it is already free.
        if (!pageHidden) { refresh(); schedule(); }
      }
    }
  }
  async function recoverReceiver() {
    if (api.demo || state.mutating || !state.recoveryChecked || !recoveryVisible()) return;
    const own = ++epoch;
    state.mutating = true;
    state.recoveryAction = true;
    state.recoveryChecked = false;
    state.recoveryError = null;
    clearTimeout(timer); api.cancel();
    if (state.recovery) state.recovery = {...state.recovery, state: 'running', error_code: null, required: true};
    try {
      const result = await api.recoverReceiver();
      if (!valid(own)) return;
      state.recovery = result;
      state.recoveryError = result.error_code || null;
      if (result.state === 'completed' && !result.required) clearRecoveryUi();
      else if (result.state === 'running') say('受信機を確認して復旧しています。完了までお待ちください。');
      else if (result.state === 'failed') say(errorText(result.error_code || 'recovery_failed')[0]);
    } catch (error) {
      if (valid(own)) {
        state.recoveryError = error.code;
        if (state.recovery) state.recovery = {...state.recovery, state: 'failed', error_code: error.code, required: true};
        noteError(error); say(errorText(error.code)[0]);
      }
    } finally {
      if (own === epoch) {
        state.mutating = false; state.recoveryAction = false;
        if (!pageHidden) { refresh(); schedule(); }
      }
    }
  }
  async function runDiagnostics() {
    const own = epoch, kind = state.diag.inputKind;
    state.diag.phase = 'running'; state.diag.error = null;
    try {
      const result = await api.diagnostics(kind);
      if (!valid(own) || state.diag.inputKind !== kind) return;
      Object.assign(state.diag, {result, phase: 'done', checkedAt: new Date().toISOString()});
    } catch (error) {
      if (valid(own) && state.diag.inputKind === kind) { state.diag.phase = 'error'; state.diag.error = error.code; noteError(error); }
    }
  }
  function startScan() {
    const range = scanRange();
    if (!range.ok || blockReason('scan')) return;
    const kind = state.diag.inputKind;
    return mutate(state.scan, () => api.startScan(range.channels, api.newRequestId(), kind), next => { state.scan.current = next; });
  }
  function stopScan() {
    if (!state.scan.current) return;
    return mutate(state.scan, () => api.stopScan(state.scan.current.id), next => { state.scan.current = next; }, state.scan.current.id);
  }
  function tune(ref) {
    if (blockReason('tune')) return;
    const w = state.watch;
    if (!api.demo && serviceByRef(ref)?.input_kind === 'live' && !state.bootstrap?.live_available) {
      w.error = 'live_not_implemented'; return;
    }
    if (active(w.session) && w.session.service_ref === ref) { setTab('watch', true); return; }
    w.targetRef = ref; w.confirmStop = false;
    w.action = active(w.session) ? 'switching' : 'starting';
    state.recordings.playing = null; state.playback = null;
    resetPlayer(); setTab('watch', true);
    return mutate(w, () => api.startSession(ref, api.newRequestId()), applySession);
  }
  function stopReceiving() {
    const w = state.watch;
    if (!active(w.session) || state.mutating) return;
    if (recording() && !w.confirmStop) {
      w.confirmStop = true; nextTick(() => refs.confirmStop?.focus()); return;
    }
    w.confirmStop = false; w.action = 'stopping'; resetPlayer();
    return mutate(w, () => api.stopSession(w.session.id), applySession, w.session.id);
  }
  function cancelStop() {
    state.watch.confirmStop = false; nextTick(() => refs.stopButton?.focus());
  }
  function startRecording() {
    if (recording() || recordBlock()) return;
    state.rec.last = null;
    return mutate(state.rec, () => api.startRecording(session().id, api.newRequestId()), next => { state.rec.current = next; });
  }
  function stopRecording() {
    if (!recording()) return;
    return mutate(state.rec, () => api.stopRecording(state.rec.current.id), next => {
      state.rec.current = next; if (!active(next)) onRecordingEnded(next);
    }, state.rec.current.id);
  }
  function startPlayback(rec) {
    if (state.mutating || !rec.playback_available) return;
    if (api.demo) { state.recordings.playing = rec.id; return; }
    resetPlayer(); state.playback = null; state.recordings.playing = rec.id;
    return mutate(state.recordings, () => api.startPlayback(rec.id), next => {
      if (state.recordings.playing === rec.id) state.playback = next;
    });
  }

  function deleteRecording(rec) {
    if (active(rec) || state.recordings.playing === rec.id || state.mutating) return;
    return mutate(state.recordings, () => api.deleteRecording(rec.id), () => {
      state.recordings.items = state.recordings.items.filter(r => r.id !== rec.id);
      state.recordings.deleteId = null;
      state.recordings.notice = '録画と再生用ファイルを削除しました。';
      say(state.recordings.notice);
    });
  }

  // Both video elements keep their DOM identity through renders and tab changes.
  // Only the visible target owns a player; lifecycle/generation changes destroy it.
  function mediaTarget(kind) {
    if (state.suspended || api.demo || offline()) return null;
    if (kind === 'watch') {
      const s = session();
      if (state.tab !== 'watch' || state.watch.action || !active(s) || s.state !== 'running' || !['ready', 'completed'].includes(s.hls?.state) || !s.hls.url) return null;
      return {key: `session:${s.id}:${state.mediaEpoch}`, url: s.hls.url, sessionId: s.id,
        started_at: s.started_at, ts_started_at: s.ts_started_at, ready_at: s.hls.ready_at};
    }
    const p = state.playback;
    if (state.tab !== 'recordings' || !p || p.recording_id !== state.recordings.playing || !['ready', 'completed'].includes(p.state) || !p.url) return null;
    return {key: `playback:${p.id}`, url: p.url, started_at: p.started_at, ready_at: p.ready_at};
  }
  const MediaView = {
    props: ['kind'],
    setup(props) {
      let video, player;
      const stopWatch = Vue.watch(() => mediaTarget(props.kind), target => {
        if (!player) return;
        if (target) player.attach(target); else player.stop();
      }, {flush: 'post'});
      Vue.onMounted(() => {
        player = SdrPlayer.create(video, event => {
          const target = mediaTarget(props.kind);
          if (!target || event.key !== target.key) return;
          if (props.kind === 'watch') state.player = {...event, sessionId: target.sessionId};
          else state.playbackPlayer = event;
        });
        const target = mediaTarget(props.kind); if (target) player.attach(target);
      });
      Vue.onBeforeUnmount(() => { stopWatch(); player?.destroy(); });
      return () => el('video', {ref: node => { video = node; }, controls: true, playsinline: true,
        preload: 'none', class: 'av-player', 'aria-label': props.kind === 'watch' ? '視聴プレイヤー' : '録画プレイヤー'});
    },
  };

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

  async function start() { await refresh(); }

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
  function recoveryFailureGuidance(code) {
    if (code === 'board_unreachable') return [
      'USBの接続を確認してください。接続できない場合は、Ubuntu側でこのアプリのフォルダーに移動し、',
      el('code', null, 'docker compose logs receiver-host'),
      ' で接続準備の結果を確認してください。USB再接続後の通信設定は自動では更新されません。復旧記録を削除せず、docs/recovery.md の手順に従ってください。',
    ];
    if (code === 'device_busy') return '別の処理が受信機を使用しています。ほかの受信処理が終わるまで待ってから再試行してください。自動では停止しません。';
    if (code === 'settings_changed') return '保存していた基準値と現在の設定が一致しません。再試行せず、受信機の設定を確認してください。';
    if (code === 'recovery_evidence_missing') return '復旧の確認記録がありません。受信を開始せず、管理者が復旧状態を確認してください。';
    if (code === 'database_error') return '復旧結果を保存できませんでした。復旧できたと判断せず、サーバーと保存先を確認してから再試行してください。';
    if (code === 'recovery_failed') return 'USBの接続とホスト側の準備を確認してから、もう一度お試しください。';
    return code ? errorText(code)[1] : null;
  }
  function renderRecoveryPanel() {
    if (!recoveryVisible()) return null;
    const running = recoveryRunning();
    const code = state.recoveryError || state.recovery?.error_code;
    const title = running ? '受信機を確認して復旧しています…'
      : code ? errorText(code)[0] : '受信機の復旧が必要です';
    const body = running
      ? '最大30秒で受信機の設定を確認して元に戻します。新しい受信やスキャンは自動で開始しません。'
      : '前回の受信後に、受信機の設定を元に戻せたか確認できませんでした。サーバーを再起動するだけでは、視聴・スキャンの制限は解除されません。';
    return el('section', {class: ['notice', 'tone-danger'], role: 'alert', 'aria-labelledby': 'recovery-title'},
      el('p', {class: 'notice-title', id: 'recovery-title'}, title),
      el('p', null, body),
      code && !running ? el('p', {class: 'hint'}, recoveryFailureGuidance(code)) : null,
      !running ? el('ol', {class: 'recovery-steps'},
        el('li', null, '受信機に異常な発熱がないことを確認してください。異常があれば操作を止めてください。'),
        el('li', null, 'ボードのSLAVE端子につながるUSBケーブルをいったん抜いて差し直してください。RFケーブルは接続したままにしてください。'),
        el('li', null, '確認欄にチェックを入れて、「受信機を確認して復旧する」を押してください。')) : null,
      !running ? el('label', {class: 'radio'},
        el('input', {type: 'checkbox', checked: state.recoveryChecked, disabled: state.mutating,
          onChange: event => { state.recoveryChecked = event.target.checked; }}),
        '異常な発熱がないこととUSBの接続を確認しました') : null,
      el('div', {class: 'actions'}, button(running ? '復旧しています…' : '受信機を確認して復旧する', recoverReceiver,
        {kind: 'primary', disabled: running || state.mutating || !state.recoveryChecked})),
    );
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
          el('span', {class: 'demo-tag'}, api.demo ? '画面だけのデモ' : 'API接続')),
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
    if (!demo) return null;
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
    if (state.globalError) list.push(errorNotice(state.globalError));
    if (state.recoveryNotice && !restoreBlocked()) list.push(notice('ok', '受信機の復旧が完了しました',
      '視聴タブで局を選ぶと受信を再開できます。録画は自動再開しません。', [
        button('視聴タブを開く', () => { state.recoveryNotice = false; setTab('watch', true); }, {kind: 'primary'}),
        button('閉じる', () => { state.recoveryNotice = false; }, {kind: 'secondary'}),
      ]));
    const recoveryPanel = renderRecoveryPanel();
    if (recoveryPanel) list.push(recoveryPanel);
    if (offline()) {
      list.push(notice('danger', 'サーバーと通信できません',
        `自動的に再接続を試みています。${state.conn.lastOkAt
          ? `表示は最後に通信できたとき（${timeFormat.format(new Date(state.conn.lastOkAt))}）の内容です。`
          : 'まだサーバーから情報を取得できていません。'}録画中だった場合も、録画はサーバー側の期限で停止します。`,
        [button('今すぐ再接続する', refresh, {kind: 'secondary'})]));
    }
    if (restoreBlocked() && !recoveryPanel) {
      list.push(notice('danger', '受信機の設定を元に戻せたか確認できていません',
        '前回の受信の後、受信機の設定を元に戻せたかを確認できませんでした。安全のため、確認が済むまで新しい受信とスキャンは開始できません。受信機の状態を確認してから、接続を確認し直してください。'));
    }
    if (storageLow()) {
      list.push(notice('warn', '保存先の空き容量が足りません',
        '接続の確認で保存先の空き容量を確認してください。正確な空き容量と録画可否は開始時にサーバーが判定します。'));
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
        el('legend', null, '接続確認・スキャンの入力元'),
        Object.entries(INPUT_KINDS).map(([kind, info]) => el('label', {class: 'radio'},
          el('input', {type: 'radio', name: 'diag-input', value: kind, checked: d.inputKind === kind,
            disabled: scanning() || state.scan.pending || (!api.demo && kind === 'live' && !state.bootstrap?.live_available),
            onChange: () => selectInput(kind)}),
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
    const check = scan.input_kind === 'live' ? '受信ボードの接続・RF配線と、登録した受信設定' : '入力ファイルとスキャンの範囲';
    if (scan.state === 'failed') {
      return {tone: 'danger', title: 'スキャンを完了できませんでした', body: `理由：${END_REASONS[scan.end_reason] || '不明'}。${kept}${check}を確認してから、もう一度お試しください。`};
    }
    if (scan.state === 'interrupted') {
      return {tone: 'warn', title: 'サーバーの再起動でスキャンが中断されました', body: `${kept}もう一度スキャンしてください。`};
    }
    if (scan.end_reason === 'requested') {
      return {tone: 'info', title: 'スキャンを中止しました', body: `${kept}中止までに検出して保存した局も一覧へ反映します。`};
    }
    if (scan.end_reason === 'deadline') {
      return {tone: 'warn', title: '時間内にすべてのチャンネルを調べられませんでした', body: `スキャンの時間上限に達しました。未実行のチャンネルを含む範囲を選び、もう一度お試しください。${kept}`};
    }
    if (!found) {
      return {tone: 'warn', title: '局が見つかりませんでした', body: `${kept}${check}を確認してから、もう一度お試しください。`};
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
      el('p', null, !api.demo && state.diag.inputKind === 'live'
        ? '選んだ範囲を実機で順に受信し、チャンネルごとの放送方式と局を調べて保存します。全範囲のスキャンには数分かかり、最大20分で終了します。'
        : !api.demo && state.diag.inputKind === 'saved_ts' ? '保存TSからのスキャンには現在対応していません。'
        : '合成TSから局を探します。チャンネルは架空の割当てで、電波の検出ではありません。最大3分で終了します。'),
      blockNotice(reason),
      el('fieldset', {class: 'choice', disabled: running},
        el('legend', null, '調べる範囲'),
        scanPresets().map(p => el('label', {class: 'radio'},
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
          range.ok ? (['live', 'synthetic'].includes(f.preset)
            ? `${range.channels.map(ch => `${ch}ch`).join('、')}（${range.channels.length}チャンネル）を調べます。`
            : `${range.from}〜${range.to}ch（${range.channels.length}チャンネル）を調べます。`) : range.message)),
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
      el('p', {class: 'meta'}, `このスキャンの入力元：${INPUT_KINDS[scan.input_kind]?.long || '表示確認用のデモ'}`),
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
          el('span', null, r.services.map(s => stationName(serviceByRef(s.id)?.name || s.name)).join('、'))))) : null,
      tech('チャンネルごとの結果を表示', scan.results.map(r => [
        `物理${r.physical_channel}ch`, `${SCAN_STAGES[r.stage] || r.stage}${r.services.length ? `（${r.services.length}サービス）` : ''}`,
      ]).concat([['スキャンID', scan.id], ['状態（state / end_reason）', `${scan.state} / ${scan.end_reason || '—'}`]])));
  }

  function renderSavedStations() {
    const live = state.services.items;
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
                  button('視聴する', () => tune(s.id), {kind: 'secondary', disabled: Boolean(reason) || Boolean(state.watch.action) || (!api.demo && s.input_kind === 'live' && !state.bootstrap?.live_available)})))))),
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
    if (state.player.phase === 'blocked') return 'autoplay_blocked';
    if (state.player.phase === 'paused') return 'paused';
    if (state.player.phase === 'buffering') return 'buffering';
    if (state.player.phase === 'failed') return 'player_failed';
    if (state.player.phase === 'showing') return 'showing';
    return 'player_preparing';
  }

  const PLAYER_TEXT = {
    autoplay_blocked: ['自動再生が許可されませんでした', 'プレイヤーの再生ボタンを押してください。音声は自動で無効にしていません。'],
    paused: ['一時停止中です', 'プレイヤーの再生ボタンで再開できます。'],
    buffering: ['再生データを待っています', ''],
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
    if (!api.demo) {
      const text = PLAYER_TEXT[phase];
      return el('div', {class: 'media-box'}, h(MediaView, {kind: 'watch', key: 'watch-video'}),
        text && phase !== 'showing' ? el('p', {class: 'hint', role: 'status'}, text.join('。')) : null);
    }
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
    const player = phase === 'showing' ? (api.demo ? 'synthetic' : 'ok') : phase === 'player_failed' ? 'failed'
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
    const metrics = session()?.receiver_metrics;
    if (phase === 'error') return errorNotice(w.error);
    if (phase === 'cas_failed') {
      return notice('danger', 'カードによる復号に失敗しました',
        '入力の映像を復号できません。外部CASとカードの接続状態を確認してください。');
    }
    if (phase === 'hls_failed') {
      return notice('danger', '映像の変換に失敗しました',
        '受信は続いています。受信を停止して選局し直してください。続く場合は「接続・スキャン」で映像の変換の状態を確認してください。');
    }
    if (phase === 'player_failed') {
      return notice('danger', 'このブラウザーで再生できませんでした',
        '受信と映像の変換は動いています。「再生をやり直す」を押してください。続く場合は別のブラウザーでお試しください。',
        [button('再生をやり直す', resetPlayer, {kind: 'secondary'})]);
    }
    if (phase === 'ended') {
      const s = session();
      if (s && (s.partial || s.state !== 'completed')) {
        return notice('warn', '受信が途中で終了しました', `理由：${END_REASONS[s.end_reason] || '不明'}。もう一度局を選ぶと受信をやり直します。`);
      }
    }
    if (w.error && phase !== 'error') return errorNotice(w.error);
    if (metrics?.rs_omitted_words_estimate > 0) {
      return notice('warn', '受信データに欠落があります',
        `復調処理で取り出せなかったデータの推定数：${metrics.rs_omitted_words_estimate}。映像や音声が乱れる場合があります。`);
    }
    return null;
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
        el('p', {class: 'hint', id: 'rec-help'}, block || '最大5分間保存します。入力の残量や保存先をサーバーが確認してから開始します。期限で自動停止します。'),
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
              disabled: Boolean(reason) || busy || (!api.demo && s.input_kind === 'live' && !state.bootstrap?.live_available), 'aria-describedby': reason ? 'picker-block' : undefined,
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
        el('div', {class: 'watch-main', key: 'watch-main'},
          el('section', {class: 'panel', 'aria-labelledby': 'now-title'},
            el('div', {class: 'now-title-row'},
              el('h3', {id: 'now-title', class: ['now-title', target && !target.name ? 'unknown' : null]},
                target ? stationName(target.name) : '局が選ばれていません'),
              target ? inputBadge(target.kind) : null),
            renderPlayer(phase),
            api.demo && phase === 'showing' ? el('p', {class: 'player-note'}, '画面だけの模擬デモです。映像・音声は再生していません。通常の画面へ戻ると合成TSを操作できます。') : null,
            phaseNotice(phase),
            s ? steps(phase) : null,
            active(s) && s.state === 'running' ? el('p', {class: 'meta'},
              `期限までの残りは約${clock(s.remaining_seconds)}です（表示用の推定）。入力ファイルが終わると先に停止します。`) : null,
            s ? el('div', {class: 'actions'},
              w.confirmStop ? el('div', {class: 'confirm', role: 'group', 'aria-label': '受信停止の確認',
                onKeydown: e => { if (e.key === 'Escape') cancelStop(); }},
                el('p', null, '録画中です。受信を停止すると録画は途中終了になり、再生・ダウンロードできません。'),
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
              ['最初のTS', s.ts_started_at], ['HLS準備完了', s.hls?.ready_at],
              ['最初のplayingイベント', state.player.observation?.playing_at],
              ['プレイヤーの経路', state.player.observation?.route],
              ['HLSの失敗段階と理由', s.hls?.error_code ? `${s.hls.error_stage} / ${s.hls.error_code}` : null],
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
    if (r.state === 'completed' && !api.demo && !r.file_available) return chip('warn', '!', 'ファイルがありません');
    if (r.state === 'running') return chip('rec', '●', '録画中');
    if (r.state === 'interrupted') return chip('warn', '!', '中断（途中まで）');
    if (r.partial || r.state === 'failed') return chip('warn', '!', '途中で終了');
    return r.state === 'completed' ? chip('ok', '✓', '完了') : chip('warn', '…', '未完了');
  }

  function renderPlayback() {
    const id = state.recordings.playing;
    const r = state.recordings.items.find(x => x.id === id);
    const p = state.playback;
    return el('section', {class: 'panel playback', hidden: !id, 'aria-labelledby': 'playback-title'},
      el('h3', {id: 'playback-title'}, `録画の再生：${stationName(r?.service_name)}`),
      api.demo ? el('p', null, '画面だけのデモです。映像・音声は再生しません。')
        : h(MediaView, {kind: 'playback', key: 'playback-video'}),
      errorNotice(state.recordings.error),
      p?.state === 'failed' || p?.state === 'interrupted'
        ? notice('danger', '録画の再生用ファイルを作れませんでした',
          `段階：${p.error_stage || '不明'}、理由：${p.error_code || p.state}。録画そのものの状態とは別です。自動では再試行しません。`)
        : !p?.url && !api.demo ? el('p', {role: 'status'}, '録画の再生を準備しています…') : null,
      state.playbackPlayer.phase === 'blocked' ? el('p', {role: 'status'}, '自動再生が許可されませんでした。プレイヤーの再生ボタンを押してください。') : null,
      state.playbackPlayer.phase === 'failed' ? notice('danger', 'ブラウザーで録画を再生できませんでした', '録画そのものの失敗ではありません。') : null,
      button('再生を閉じる', () => { state.recordings.playing = null; state.playback = null; resetPlayer(); }, {kind: 'secondary'}));
  }

  function renderRecordingsTab() {
    const list = state.recordings;
    return [
      !list.playing ? errorNotice(list.error) : null,
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
              r.state === 'completed' && !api.demo && !r.file_available ? el('p', {class: 'hint'}, '完了の記録はありますが、ファイルがないため再生・ダウンロードできません。') : r.state === 'running' ? el('p', {class: 'hint'}, `録画中です。残り${clock(r.remaining_seconds)}で自動的に停止します。`)
                : el('p', {class: 'hint'}, partial
                  ? `${reason || '不明な理由'}のため途中で終了しました。途中までのデータは再生・ダウンロードの対象外です。`
                  : `${reason || '終了'}。`),
              el('div', {class: 'actions'},
                button('再生する', () => startPlayback(r), {kind: 'secondary', disabled: !r.playback_available || state.mutating || offline()}),
                r.download_available && !api.demo
                  ? el('a', {class: 'btn btn-secondary', href: r.download_url, download: ''}, 'TSファイルをダウンロード')
                  : button('TSファイルをダウンロード', () => { list.notice = '画面だけのデモのため、ダウンロードしません。'; }, {kind: 'secondary', disabled: !r.download_available}),
                !api.demo ? button(r.deletion_pending ? '削除を再試行する' : '削除する', () => { list.deleteId = r.id; },
                  {kind: 'danger', disabled: active(r) || list.playing === r.id || state.mutating || offline()}) : null),
              list.playing === r.id ? el('p', {class: 'hint'}, '削除するには、先に再生を閉じてください。') : null,
              r.deletion_pending ? el('p', {class: 'hint'}, 'ファイルの削除が完了していません。削除を再試行してください。') : null,
              list.deleteId === r.id ? notice('warn', 'この録画を削除しますか？',
                '録画TSと再生用ファイルを削除します。元に戻せません。他の画面でこの録画を再生している場合も、続けて再生できなくなります。', [
                  button('録画を削除する', () => deleteRecording(r), {kind: 'danger', disabled: state.mutating || offline()}),
                  button('削除をキャンセルする', () => { list.deleteId = null; }, {kind: 'secondary'}),
                ]) : null,
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
        epoch += 1; api.cancel(); state.suspended = true; resetPlayer();
        state.mutating = false; state.recoveryAction = false; state.watch.action = null;
        state.scan.pending = false; state.rec.pending = false;
        if (state.diag.phase === 'running') state.diag.phase = 'idle';
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
          }, tab.id === 'scan' ? renderScanTab() : tab.id === 'watch' ? renderWatchTab() : renderRecordingsTab()))),
        el('footer', {class: 'container footer'},
          el('p', null, 'SDR DTV PoC — 実験的なOpen Source PoCです。対応する機器・環境は検証済みの範囲に限られます。'),
          el('p', null, el('a', {href: '/openapi.json'}, 'APIの仕様（OpenAPI）'), '　',
            el('a', {href: '/static/vendor/manifest.json'}, '同梱しているライブラリの一覧'))),
        el('p', {class: 'visually-hidden', role: 'status', 'aria-live': 'polite'}, state.announce));
    },
  };

  createApp(App).mount('#app');
})();
