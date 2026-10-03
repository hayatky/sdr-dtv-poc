// SPDX-License-Identifier: GPL-3.0-or-later
(function () {
  'use strict';
  function create(video, report) {
    let generation = 0, key = null, hls = null, timer = null, remove = [];
    let observation = null;
    function stop() {
      generation += 1; key = null;
      clearInterval(timer); timer = null;
      for (const fn of remove) fn(); remove = [];
      if (hls) hls.destroy(); hls = null;
      video.pause(); video.removeAttribute('src'); video.load();
      observation = null; video.sdrObservation = null;
    }
    function attach(target, forceHls = false) {
      if (key === target.key && !forceHls) return;
      stop(); key = target.key;
      const own = generation;
      let failed = false;
      const valid = () => generation === own && key === target.key;
      observation = {key, session_id: target.sessionId || null, started_at: target.started_at || null,
        ts_started_at: target.ts_started_at || null, ready_at: target.ready_at || null,
        playing_at: null, samples: [], route: null, native_unsupported: forceHls};
      const emit = (phase, error = null) => {
        if (!valid() || failed) return;
        if (phase === 'failed') failed = true;
        report({phase, error, key, observation});
      };
      function listen(name, fn) {
        const handler = () => { if (valid()) fn(); };
        video.addEventListener(name, handler);
        remove.push(() => video.removeEventListener(name, handler));
      }
      async function play() {
        if (!valid() || failed) return;
        try { await video.play(); }
        catch (error) {
          if (!valid()) return;
          if (error.name === 'NotSupportedError' && fallback()) return;
          emit(error.name === 'NotAllowedError' ? 'blocked' : 'failed', error.name);
        }
      }
      function fallback() {
        if (!valid() || observation.route !== 'native' || !window.Hls?.isSupported()) return false;
        attach(target, true); return true;
      }
      listen('playing', () => {
        observation.playing_at ||= new Date().toISOString(); emit('showing');
      });
      listen('waiting', () => emit('buffering'));
      listen('pause', () => emit('paused'));
      listen('ended', () => emit('ended'));
      listen('error', () => {
        if (video.error?.code === 4 && fallback()) return;
        emit('failed', `media_${video.error?.code || 'unknown'}`);
      });
      listen('loadedmetadata', play);
      timer = setInterval(() => {
        if (!valid()) return;
        const end = video.buffered.length ? video.buffered.end(video.buffered.length - 1) : null;
        observation.samples.push({at: new Date().toISOString(), current_time: video.currentTime,
          buffered_end: end, live_sync_position: hls?.liveSyncPosition ?? null,
          frames: video.getVideoPlaybackQuality?.().totalVideoFrames ?? null});
        if (observation.samples.length > 30) observation.samples.shift();
        // Bounded local observations, exposed on the stable video for inspection.
        video.sdrObservation = structuredClone(observation);
      }, 1000);
      emit('preparing');
      if (!forceHls && video.canPlayType('application/vnd.apple.mpegurl')) {
        observation.route = 'native'; video.src = target.url; play();
      } else if (window.Hls?.isSupported()) {
        observation.route = 'hls.js';
        hls = new Hls({enableWorker: false, maxBufferLength: 20, maxMaxBufferLength: 30, backBufferLength: 10});
        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (!valid() || !data.fatal) return;
          hls.stopLoad(); emit('failed', data.type);
        });
        hls.on(Hls.Events.MANIFEST_PARSED, play);
        hls.attachMedia(video); hls.loadSource(target.url);
      } else emit('failed', 'hls_unsupported');
    }
    return {attach, stop, destroy: stop};
  }
  window.SdrPlayer = {create};
})();
