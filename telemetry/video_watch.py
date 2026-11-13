"""A small browser player for a stored session, never an arbitrary file proxy."""
from __future__ import annotations

from html import escape
import json
import secrets
from urllib.parse import quote, urlparse


def original_video_url(source):
    if not isinstance(source, dict):
        return None
    for value in (source.get("original_url"), source.get("url")):
        if not isinstance(value, str) or not value or len(value) > 4096:
            continue
        if "\\" in value or any(character.isspace() or ord(character) < 32 for character in value):
            continue
        try:
            parsed = urlparse(value)
            port = parsed.port
            if (parsed.scheme in {"https", "http"} and parsed.hostname
                    and (port is None or 1 <= port <= 65535)
                    and not parsed.username and not parsed.password):
                return value
        except ValueError:
            continue
    return None


def watch_document(session):
    identifier = quote(session["id"], safe="")
    source = session.get("source") or {}
    title = escape(str(session.get("name") or (source.get("name") if isinstance(source, dict) else None) or "Recording"))
    media = f"/api/sessions/{identifier}/video/media"
    audio = f"/api/sessions/{identifier}/audio"
    nonce = secrets.token_urlsafe(18)
    # All URLs are constructed from the looked-up session id. Neither source
    # paths nor original remote URLs are embedded in this local player.
    script = r"""
const video = document.querySelector('video'), sound = document.querySelector('audio');
const status = document.querySelector('[role=status]'), button = document.querySelector('button');
let soundStart = 0, prepared = false;
const positionSound = () => {
  if (!prepared || sound.readyState < 1) return;
  const time = Math.max(0, video.currentTime - soundStart);
  if (Math.abs(sound.currentTime - time) > .15) sound.currentTime = time;
  sound.playbackRate = video.playbackRate;
};
video.addEventListener('play', () => {positionSound(); if(prepared) sound.play().catch(() => {status.textContent='Press play on the sound control to enable sound.';});});
video.addEventListener('pause', () => sound.pause());
video.addEventListener('seeking', () => {sound.pause(); positionSound();});
video.addEventListener('seeked', () => {positionSound(); if(prepared && !video.paused) sound.play().catch(()=>{});});
video.addEventListener('timeupdate', positionSound);
video.addEventListener('ratechange', positionSound);
video.addEventListener('error', () => {status.textContent='This browser cannot play the recording format. Download the recording below and open it in a video player.';});
button.addEventListener('click', async () => {
  button.disabled = true; status.textContent = 'Preparing compatible sound…';
  try {
    let response = await fetch(AUDIO_URL, {method:'POST'});
    let result = await response.json();
    if (!response.ok) throw Error(result.detail || 'Sound could not be prepared.');
    while (result.state === 'preparing') {
      await new Promise(resolve => setTimeout(resolve, 750));
      response = await fetch(AUDIO_URL);
      result = await response.json();
      if (!response.ok) throw Error(result.detail || 'Sound could not be prepared.');
    }
    if (result.state !== 'ready' || !result.url) throw Error(result.message || 'No compatible sound is available.');
    soundStart = Number(result.source_start || 0);
    sound.src = result.url; sound.hidden = false; video.muted = true; prepared = true;
    sound.addEventListener('loadedmetadata', positionSound, {once:true});
    status.textContent = 'Compatible sound ready. Its volume control is below the video.';
    if (!video.paused) await sound.play();
  } catch (error) {status.textContent = error.message; button.disabled = false;}
});
""".replace("AUDIO_URL", json.dumps(audio))
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · ACCC Telemetry</title><link rel="icon" type="image/svg+xml" href="/static/brand/accc-mark.svg">
<style>body{{margin:0;background:#101512;color:#edf2e7;font:16px system-ui,sans-serif}}main{{max-width:1100px;margin:32px auto;padding:0 20px}}h1{{font-size:24px;overflow-wrap:anywhere}}video{{display:block;width:100%;max-height:75vh;background:#000}}audio{{width:100%;margin-top:12px}}button,a{{color:#d5e991}}button{{background:#223022;border:1px solid #758341;border-radius:5px;padding:10px;cursor:pointer}}p{{line-height:1.5}}[hidden]{{display:none}}.brand{{display:flex;align-items:center;gap:12px}}.actions{{display:flex;gap:20px;align-items:center;flex-wrap:wrap}}</style></head>
<body><main><p class="brand"><img src="/static/brand/accc-mark.svg" width="44" height="44" alt="">ACCC Telemetry · Recording</p><h1>{title}</h1><video controls playsinline preload="metadata" src="{media}"></video><audio controls hidden></audio><p role="status">Play the full recording with sound. Keep ACCC Telemetry open while watching.</p><div class="actions"><button type="button">Use compatible sound</button><a href="{media}" download>Download recording</a></div></main><script nonce="{nonce}">{script}</script></body></html>"""
    headers = {"Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src 'self'; media-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
               "X-Frame-Options": "DENY", "Cache-Control": "no-store"}
    return document, headers
