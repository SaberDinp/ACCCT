"""Real local watch page/video/audio playback, using generated media only."""
import asyncio
from fractions import Fraction
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import av
import numpy as np
from playwright.async_api import async_playwright, expect
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from main import create_app


async def wait_for(page, expression):
    # Playwright's injected polling helper uses eval, which this watch page's
    # CSP intentionally forbids. Direct DevTools evaluation needs no CSP bypass.
    for _ in range(200):
        if await page.evaluate(expression):
            return
        await asyncio.sleep(.05)
    raise AssertionError('Player did not reach expected state: ' + expression)


def make_video_audio(path):
    with av.open(str(path), 'w') as output:
        video = output.add_stream('libx264', rate=30)
        video.width, video.height, video.pix_fmt = 320, 180, 'yuv420p'
        video.options = {'preset': 'ultrafast', 'crf': '25'}
        audio = output.add_stream('aac', rate=48000)
        audio.layout = 'stereo'
        for index in range(120):
            pixels = np.full((180, 320, 3), (30, 65, 90), np.uint8)
            pixels[:, index % 300:index % 300 + 20] = (180, 200, 235)
            frame = av.VideoFrame.from_ndarray(pixels, format='bgr24')
            frame.pts, frame.time_base = index, Fraction(1, 30)
            for packet in video.encode(frame):
                output.mux(packet)
            samples = np.arange(index * 1600, (index + 1) * 1600)
            waveform = np.tile((.1 * np.sin(2 * np.pi * 440 * samples / 48000)).astype(np.float32), (2, 1))
            sound = av.AudioFrame.from_ndarray(waveform, format='fltp', layout='stereo')
            sound.pts, sound.time_base, sound.sample_rate = index * 1600, Fraction(1, 48000), 48000
            for packet in audio.encode(sound):
                output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None):
                output.mux(packet)


async def main():
    output = ROOT / 'artifacts/video-watch-browser'
    output.mkdir(parents=True, exist_ok=True)
    checks, errors, violations = {}, [], []
    with tempfile.TemporaryDirectory(prefix='accc watch browser ') as folder:
        root = Path(folder).resolve()
        clip = root / 'generated-video-audio.mp4'
        make_video_audio(clip)
        app = create_app(root=root)
        session = app.state.engine.store.create_session({'kind': 'file', 'path': str(clip), 'name': 'Generated full recording with sound'})
        app.state.engine.store.save_summary(session, {}, status='completed')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        for _ in range(500):
            if server.started:
                break
            time.sleep(.01)
        assert server.started
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(viewport={'width': 1280, 'height': 900})
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.add_init_script('document.addEventListener("securitypolicyviolation",event=>{window.watchViolations||=[];window.watchViolations.push(event.violatedDirective);});')
                await page.goto(f'http://127.0.0.1:{port}/api/sessions/{session}/video/watch')
                await expect(page.locator('h1')).to_have_text('Generated full recording with sound')
                await wait_for(page, '() => document.querySelector("video").readyState>=2')
                assert await page.locator('video').evaluate('video=>video.controls && !video.muted')
                await page.locator('video').evaluate('video=>video.play()')
                await wait_for(page, '() => document.querySelector("video").currentTime>.5')
                await page.locator('video').evaluate('video=>video.pause()')
                audio_bytes = await page.locator('video').evaluate('video=>video.webkitAudioDecodedByteCount')
                assert audio_bytes > 0, audio_bytes
                assert app.state.review_audio.worker is None
                checks['full_video_decodes_native_sound_without_preparing_audio'] = {'decoded_audio_bytes': audio_bytes}
                await page.get_by_role('button', name='Use compatible sound').click()
                await expect(page.get_by_role('status')).to_contain_text('Compatible sound ready', timeout=10000)
                await wait_for(page, '() => document.querySelector("audio").readyState>=2')
                await expect(page.locator('audio')).to_be_visible()
                await page.locator('video').evaluate('video=>{video.currentTime=2;return video.play();}')
                await wait_for(page, '''() => {
                    const video=document.querySelector('video'),audio=document.querySelector('audio');
                    return video.currentTime>2.25&&!audio.paused&&Math.abs(video.currentTime-audio.currentTime)<.2;
                }''')
                await page.locator('video').evaluate('video=>video.pause()')
                await wait_for(page, '() => document.querySelector("audio").paused')
                checks['compatible_audio_prepares_only_on_click_and_tracks_video_seek_play_pause'] = True
                await page.screenshot(path=str(output/'local-watch.png'), full_page=True)
                violations = await page.evaluate('window.watchViolations||[]')
                assert not violations, violations
                assert not errors, errors
                await browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            assert not thread.is_alive()
    report = {'checks': checks, 'page_errors': errors, 'csp_violations': violations, 'real_library_mutated': False}
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
