# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional browser test tool; no product UI, Node/npm install, or project dependency."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://localhost:18324")
    parser.add_argument("--chromium", type=Path, required=True)
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(args.chromium),
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        page = browser.new_page()
        page.goto(args.origin)
        page.add_script_tag(url=args.origin + "/static/vendor/hls.min.js")
        result = page.evaluate("""async () => {
          const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
          const token = (await (await fetch('/api/bootstrap')).json()).csrf_token;
          const api = async (url, body) => {
            const response = await fetch(url, body === undefined ? {} : {
              method: 'POST', headers: {'Content-Type':'application/json','X-CSRF-Token':token}, body: JSON.stringify(body)});
            if (!response.ok) throw new Error('API failed: ' + response.status);
            return response.json();
          };
          const wait = async (url, predicate) => {
            for (let i=0;i<300;i++) { const value=await api(url); if(predicate(value)) return value; await delay(100); }
            throw new Error('poll deadline');
          };
          const session = await api('/api/sessions', {request_id:crypto.randomUUID(),duration_seconds:600});
          const players=[];
          const player = async (url, live = false) => {
            const video = document.createElement('video'); video.controls=true; document.body.append(video);
            const hls = new Hls(); hls.attachMedia(video); hls.loadSource(url);
            const context = new AudioContext(); const analyser=context.createAnalyser();
            const gain=context.createGain(); gain.gain.value=0;
            context.createMediaElementSource(video).connect(analyser); analyser.connect(gain); gain.connect(context.destination);
            await context.resume();
            const pcm=new Float32Array(analyser.fftSize); let peak=0;
            const timer=setInterval(()=>{analyser.getFloatTimeDomainData(pcm); for(const sample of pcm) peak=Math.max(peak,Math.abs(sample));},20);
            players.push({video,hls,context,timer});
            await Promise.race([video.play(), delay(15000).then(()=>{throw new Error('play deadline');})]);
            const warmup=[];
            if(live) for(let n=0;n<7;n++){await delay(1000);warmup.push(video.currentTime);}
            const samples=[];
            for(let n=0;n<5;n++){await delay(1000);samples.push({time:video.currentTime,lag:hls.liveSyncPosition == null ? null : hls.liveSyncPosition-video.currentTime});}
            const frames=video.getVideoPlaybackQuality().totalVideoFrames;
            if(frames===0 || peak<0.001 || samples[4].time-samples[0].time<3) throw new Error('A/V did not advance: '+JSON.stringify({frames,peak,samples,audioState:context.state,readyState:video.readyState,error:video.error?.code}));
            return {frames,audio_peak:peak,warmup,samples};
          };
          try {
            const ready=await wait('/api/sessions/'+session.id,x=>x.hls.state==='ready'||x.hls.state==='failed');
            if(ready.hls.state!=='ready') throw new Error('HLS failed');
            const recording=await api('/api/recordings',{request_id:crypto.randomUUID(),session_id:session.id,duration_seconds:8});
            const live=await player(ready.hls.url,true);
            const path='/api/recordings/'+recording.id;
            const done=await wait(path,x=>x.state!=='running');
            if(done.state!=='completed') throw new Error('recording failed');
            await api(path+'/playback',{});
            const playback=await wait(path+'/playback',x=>x.state==='completed'||x.state==='failed');
            if(playback.state!=='completed') throw new Error('playback conversion failed');
            const recorded=await player(playback.url);
            return {live,recorded,human_playback:'not_checked'};
          } finally {
            for(const p of players){clearInterval(p.timer);p.video.pause();p.hls.destroy();await p.context.close();}
            await api('/api/sessions/'+session.id+'/stop',{});
            await wait('/api/sessions/'+session.id,x=>['completed','failed'].includes(x.state));
          }
        }""")
        print(json.dumps({"browser": browser.version, **result}, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
