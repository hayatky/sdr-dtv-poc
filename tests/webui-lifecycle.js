// SPDX-License-Identifier: GPL-3.0-or-later
// Browser-executed regressions with controlled transport/media failures.
async () => {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  const originalFetch = window.fetch, originalHls = window.Hls;
  const saved = sessionStorage.getItem('sdr-uncertain');
  sessionStorage.removeItem('sdr-uncertain');
  const requests = [];
  let failure = 'lost', accepted = null, hold = null;
  const json = (value, status = 200) => new Response(JSON.stringify(value), {status});
  window.fetch = async (path, options) => {
    requests.push({path, options});
    if (hold) return new Promise(resolve => { hold.resolve = resolve; });
    if (path === '/api/bootstrap') return json({csrf_token: 'synthetic-test-token'});
    if (options.method === 'POST') {
      const body = JSON.parse(options.body);
      if (failure === '403') return json({code: 'operation_protection_required'}, 403);
      accepted ||= {id: crypto.randomUUID(), ...body, state:'running', deadline_at:new Date().toISOString(), service:null};
      if (failure === 'lost') { failure = ''; throw new TypeError('network failure'); }
      return json(accepted, 202);
    }
    if (path === '/api/sessions') return json(accepted ? [accepted] : []);
    return json([]);
  };
  try {
    const api = SdrApi.create({mode:'http'});
    await api.bootstrap();
    try { await api.startSession('saved-service', api.newRequestId()); } catch (e) { assert(e.status === 0, 'network classification'); }
    const first = JSON.parse(requests.at(-1).options.body);
    await api.startSession('saved-service', api.newRequestId());
    assert(JSON.parse(requests.at(-1).options.body).request_id === first.request_id, 'explicit retry must retain request id');
    failure = 'lost'; accepted = null;
    try { await api.startSession('saved-service', api.newRequestId()); } catch (_) { /* expected */ }
    await api.status();
    assert(Object.keys(JSON.parse(sessionStorage.getItem('sdr-uncertain'))).length === 0, 'read reconciliation clears uncertain start');
    const count = requests.length;
    failure = '403';
    try { await api.startRecording('session', api.newRequestId()); } catch (e) { assert(e.status === 403, 'CSRF classification'); }
    assert(requests.length === count + 1, '403 must not bootstrap/replay mutation');
    hold = {};
    const pending = api.bootstrap().catch(e => e.code);
    api.cancel(); hold.resolve(json({csrf_token:'late-token'}));
    assert(await pending === 'cancelled', 'late read must not update token after cancellation');

    class Video extends EventTarget {
      constructor(native) { super(); this.native = native; this.paused = true; this.buffered = {length:0}; this.handlers = []; }
      canPlayType() { return this.native ? 'maybe' : ''; }
      addEventListener(name, fn) { this.handlers.push({name, fn}); super.addEventListener(name, fn); }
      play() { return Promise.reject(new DOMException('test', 'NotAllowedError')); }
      pause() { this.paused = true; }
      load() {}
      removeAttribute() { this.src = ''; }
    }
    const native = new Video(true), nativeEvents = [];
    const player = SdrPlayer.create(native, e => nativeEvents.push(e));
    player.attach({key:'native-a', url:'/api/artifacts/example/files/index.m3u8'});
    await tick();
    assert(nativeEvents.at(-1).phase === 'blocked', 'autoplay rejection must request user play');
    const late = native.handlers.find(h => h.name === 'playing').fn;
    player.attach({key:'native-b', url:'/api/artifacts/other/files/index.m3u8'});
    const eventCount = nativeEvents.length;
    late();
    assert(nativeEvents.length === eventCount, 'old native event must be ignored');
    player.stop(); await tick();
    assert(nativeEvents.length === eventCount, 'old play promise must be ignored');
    assert(!native.src && native.paused, 'native stop releases source');

    const instances = [];
    class Hls {
      static Events = {ERROR:'error', MANIFEST_PARSED:'manifest'};
      static isSupported() { return true; }
      constructor() { this.events = {}; instances.push(this); }
      on(event, fn) { this.events[event] = fn; }
      attachMedia() {}
      loadSource() {}
      stopLoad() { this.stopped = true; }
      destroy() { this.destroyed = true; }
    }
    window.Hls = Hls;
    const media = new Video(false), events = [];
    const hlsPlayer = SdrPlayer.create(media, e => events.push(e));
    const a = {key:'a', url:'/a'};
    hlsPlayer.attach(a); hlsPlayer.attach(a);
    assert(instances.length === 1, 'same target must not recreate HLS');
    hlsPlayer.attach({key:'b',url:'/b'});
    assert(instances[0].destroyed, 'switch must destroy previous HLS');
    const before = events.length;
    instances[0].events.error(null,{fatal:true,type:'old-error'});
    assert(events.length === before, 'old HLS event must not replace current state');
    instances[1].events.error(null,{fatal:true,type:'mediaError'});
    assert(events.at(-1).phase === 'failed' && instances[1].stopped, 'fatal player error stops fetching');
    for (const name of ['waiting', 'pause', 'playing', 'ended']) media.dispatchEvent(new Event(name));
    await tick();
    assert(events.at(-1).phase === 'failed', 'fatal failure must survive later media events and promises');
    hlsPlayer.destroy();
    assert(instances[1].destroyed && !media.src, 'destroy releases HLS and source');
    return {transport: 'passed', native_lifecycle_stub: 'passed', hls_lifecycle_stub: 'passed'};
  } finally {
    window.fetch = originalFetch; window.Hls = originalHls;
    if (saved === null) sessionStorage.removeItem('sdr-uncertain'); else sessionStorage.setItem('sdr-uncertain', saved);
  }
}
