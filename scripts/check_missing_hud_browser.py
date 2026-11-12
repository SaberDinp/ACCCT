"""Isolated Analysis video regression with missing HUD, gaps and native media.

Uses the real comparison functions through in-memory API fixtures. No user
sessions, source controls or browser storage are changed.
"""
import asyncio
import functools
import json
from fractions import Fraction
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlparse

import av
import numpy as np
from playwright.async_api import async_playwright, expect

from check_delete_swap_browser import CURRENT, REFERENCE, PROTECTED
from browser_video_fixtures import VideoFixtures
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler

OUT = ROOT / 'artifacts/missing-hud-browser'


def make_clip(path):
    if path.exists():
        return
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=30)
        stream.width, stream.height, stream.pix_fmt = 320, 180, 'yuv420p'
        stream.options = {'preset': 'ultrafast', 'crf': '25'}
        for index in range(240):
            pixels = np.full((180, 320, 3), (30, 65, 90), np.uint8)
            pixels[:, index % 300:index % 300 + 20] = (180, 200, 235)
            frame = av.VideoFrame.from_ndarray(pixels, format='bgr24')
            frame.pts, frame.time_base = index, Fraction(1, 30)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class MissingFixtures(VideoFixtures):
    def __init__(self, clip, mode, boundary_side=REFERENCE):
        super().__init__(clip, clip)
        self.mode = mode
        del self.sessions[PROTECTED]
        for side, data in self.sessions.items():
            data['samples'] = data['samples'][:80]
            for sample in data['samples']:
                boundary_missing = side == boundary_side and (
                    mode.startswith('leading_') and sample['time'] < 1.6 or
                    mode.startswith('trailing_') and sample['time'] > 6.4)
                missing = boundary_missing or mode == 'noneboth' or (side == REFERENCE and mode in {'none', 'offset', 'speed', 'mismatch'}) or (side == REFERENCE and mode == 'gap' and 2 <= sample['time'] <= 4)
                if missing:
                    for channel in ('speed', 'gear', 'brake', 'throttle', 'steering'):
                        sample[channel] = None
                    sample.update(active=False, hud_visible=False, segment_id=None)
                    if mode == 'speed' or boundary_missing and mode.endswith('_speed'):
                        sample.update(active=True, hud_visible=True, segment_id=1, gear='3', throttle=70, brake=0)
                elif mode == 'gap' and side == REFERENCE and sample['time'] > 4:
                    sample['segment_id'] = 2
            data['session']['sample_count'] = len(data['samples'])
        if mode == 'offset':
            data = self.sessions[REFERENCE]
            data['samples'] = data['samples'][:60]
            for sample in data['samples']:
                sample['time'] += 2
            data['session']['sample_count'] = len(data['samples'])
        if mode == 'rewind':
            data = self.sessions[REFERENCE]
            data['samples'] = data['samples'][40:] + data['samples'][:40]

    async def route(self, route):
        if self.mode == 'mismatch' and urlparse(route.request.url).path == '/api/compare':
            return await route.fulfill(status=400, json={'detail': 'Comparison requires the same circuit layout. These recordings have different track labels.'})
        return await super().route(route)


async def seek(page, stamp, source_stamp=None):
    await page.locator('#trackReviewDistance').evaluate('(el, stamp) => {el.value=stamp; el.dispatchEvent(new Event("input", {bubbles:true}));}', stamp)
    await page.wait_for_function('stamp => Math.abs(document.querySelector("#trackreferenceNative").currentTime - stamp) < .12', arg=stamp if source_stamp is None else source_stamp)
    await expect(page.locator('#trackreferenceNative')).to_be_visible()


async def boundary_checks(browser, clip, url, checks, errors):
    for mode in ('leading_hud', 'leading_speed', 'trailing_hud', 'trailing_speed'):
        for side, paired in (('current', False), ('reference', False), ('current', True), ('reference', True)):
            sid = CURRENT if side == 'current' else REFERENCE
            fixture = MissingFixtures(clip, mode, sid)
            fixture.sessions[sid]['session']['metadata'].update(
                reviewed_laps=[{'id': 'boundary-fixture', 'start_time': 1.6, 'end_time': 6.4,
                                'segment_id': 1, 'boundaries_verified': False}],
                comparison_defaults={'range_id': 'boundary-fixture'})
            context = await browser.new_context(viewport={'width': 1440, 'height': 1100})
            await context.route('**/api/**', fixture.route)
            await context.add_init_script(SOCKET_FIXTURE)
            preferences = {'axis': 'distance', 'alignment_mode': 'raw'}
            for run, identifier in (('current', CURRENT), ('reference', REFERENCE)):
                preferences[run] = {'id': identifier if paired or run == side else '',
                                    'range': 'boundary-fixture' if run == side else 'custom',
                                    'start': 1.6, 'end': 6.4, 'segment': '1'}
            await context.add_init_script('''if (!sessionStorage.getItem('boundary-check')) {
                localStorage.setItem('accc.comparison.v1', %s);
                sessionStorage.setItem('boundary-check', '1');
            }''' % json.dumps(json.dumps(preferences)))
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(url)
            await page.get_by_role('button', name='Analysis', exact=True).click()
            await expect(page.locator(f'#{side}Range')).to_have_value('boundary-fixture')
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=15000)
            await expect(page.locator('#axisDistance')).to_have_attribute('aria-pressed', 'true')
            edge, value = ('Start', '1.15') if mode.startswith('leading_') else ('End', '6.85')
            await page.locator(f'#{side}{edge}').fill(value)
            await page.locator(f'#{side}{edge}').press('Tab')
            await expect(page.locator(f'#{side}Range')).to_have_value('custom')
            await expect(page.locator(f'#{side}Segment')).to_have_value('1')
            await expect(page.locator('#axisElapsed')).to_have_attribute('aria-pressed', 'true', timeout=15000)
            await expect(page.locator('#comparisonWarnings')).to_contain_text('preserve the selected IN and OUT')
            start, end = (1.15, 6.4) if mode.startswith('leading_') else (1.6, 6.85)
            elapsed = 0 if mode.startswith('leading_') else end - start
            await page.locator('#trackReviewDistance').evaluate('''(el, stamp) => {
                el.value=stamp; el.dispatchEvent(new Event('input', {bubbles:true}));
            }''', elapsed)
            await expect(page.locator(f'#track{side}Native')).to_be_visible(timeout=15000)
            await page.wait_for_function('''({side, stamp}) =>
                Math.abs(document.querySelector('#track' + side + 'Native').currentTime - stamp) < .08
            ''', arg={'side': side, 'stamp': start if elapsed == 0 else end})
            if elapsed == 0:
                await expect(page.locator(f'#track{side}Values')).to_contain_text(
                    'HUD telemetry unavailable' if mode.endswith('_hud') else '-- km/h')
                await page.locator('#trackReviewPlay').click()
                await page.wait_for_function('''side =>
                    document.querySelector('#track' + side + 'Native').currentTime > 1.8
                ''', arg=side, timeout=7000)
                await page.locator('#trackReviewPlay').click()
            else:
                await expect(page.locator(f'#track{side}Values')).to_contain_text('End of selected range')
            await page.reload()
            await page.get_by_role('button', name='Analysis', exact=True).click()
            await expect(page.locator(f'#{side}{edge}')).to_have_value(value, timeout=15000)
            await expect(page.locator(f'#{side}Range')).to_have_value('custom')
            await expect(page.locator(f'#{side}Segment')).to_have_value('1')
            await expect(page.locator('#axisElapsed')).to_have_attribute('aria-pressed', 'true')
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=15000)
            await page.wait_for_function('''({side, stamp}) =>
                Math.abs(document.querySelector('#track' + side + 'Native').currentTime - stamp) < .08
            ''', arg={'side': side, 'stamp': start})
            assert not fixture.unexpected, fixture.unexpected
            checks[mode + '_' + side + ('_paired' if paired else '_single')] = True
            await context.close()


async def main():
    OUT.mkdir(exist_ok=True)
    clip = OUT / 'source.mp4'
    make_clip(clip)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors = {}, []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            for mode, paired in [('none', False), ('none', True), ('noneboth', True), ('offset', False), ('speed', True), ('gap', True), ('mismatch', True), ('rewind', False)]:
                fixture = MissingFixtures(clip, mode)
                context = await browser.new_context(viewport={'width':1440, 'height':1100})
                await context.route('**/api/**', fixture.route)
                await context.add_init_script(SOCKET_FIXTURE)
                preferences = {'axis': 'elapsed' if mode == 'gap' else 'distance',
                               'current': {'id': CURRENT if paired else '', 'range':'full'},
                               'reference': {'id':REFERENCE, 'range':'full'}}
                if mode == 'offset':
                    preferences['reference'].update(range='custom', start=0, end=9, segment='')
                await context.add_init_script('localStorage.setItem("accc.comparison.v1", %s)' % json.dumps(json.dumps(preferences)))
                page = await context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{server.server_port}')
                await page.get_by_role('button', name='Analysis', exact=True).click()
                await expect(page.locator('#referenceSession')).to_have_value(REFERENCE)
                if mode in {'mismatch', 'rewind'}:
                    message = 'different track labels' if mode == 'mismatch' else 'crosses a seek'
                    await expect(page.locator('#comparisonStatus')).to_contain_text(message, timeout=15000)
                    await expect(page.locator('#trackReviewPlay')).to_be_disabled()
                    checks[mode + '_still_rejected'] = True
                    await context.close()
                    continue
                await expect(page.locator('#axisElapsed')).to_have_attribute('aria-pressed', 'true', timeout=15000)
                await expect(page.locator('#trackreferenceNative')).to_be_visible(timeout=15000)
                await expect(page.locator('#trackReviewPlay')).to_be_enabled()
                if mode == 'gap':
                    await expect(page.locator('#comparisonReferenceSummary')).to_contain_text('(partial distance)')
                    await expect(page.locator('#comparisonReferenceSummary')).to_have_attribute(
                        'title', 'Distance excludes movement where speed data is missing.')
                    await expect(page.locator('#comparisonCurrentSummary')).not_to_contain_text('partial')
                    await expect(page.locator('#comparisonCurrentSummary')).to_have_attribute('title', '')
                    checks['partial_distance_label_without_changing_complete_summary'] = True
                source_stamp = 5 if mode == 'offset' else 3
                await seek(page, 3, source_stamp)
                if mode in {'none', 'noneboth', 'offset', 'gap'}:
                    await expect(page.locator('#trackreferenceValues')).to_contain_text('HUD telemetry unavailable')
                if paired:
                    await expect(page.locator('#trackcurrentNative')).to_be_visible()
                    await expect(page.locator('#trackcurrentValues')).to_contain_text('HUD telemetry unavailable' if mode == 'noneboth' else 'km/h')
                await page.locator('#trackReviewPlay').click()
                await page.wait_for_function('stamp => document.querySelector("#trackreferenceNative").currentTime > stamp + .7', arg=source_stamp, timeout=7000)
                await expect(page.locator('#trackreferenceNative')).to_be_visible()
                await page.locator('#trackReviewPlay').click()
                await page.wait_for_function('document.querySelector("#trackreferenceNative").paused')
                await seek(page, 3, source_stamp)
                await page.locator('#trackreferenceInspect').click()
                await expect(page.locator('#alignmentDialog')).to_be_visible()
                await expect(page.locator('#alignmentImage')).to_be_visible()
                await page.locator('#closeAlignment').click()
                if mode == 'none' and not paired:
                    await expect(page.locator('#comparisonStatus')).to_contain_text('No usable HUD telemetry')
                    await page.screenshot(path=str(OUT/'no-hud-video.png'), full_page=True)
                    fixture.failure = True
                    await page.locator('#referenceSession').select_option('')
                    await page.locator('#referenceSession').select_option(REFERENCE)
                    await expect(page.locator('#trackreferenceImage')).to_be_visible(timeout=15000)
                    checks['jpeg_fallback_without_hud'] = True
                assert not fixture.unexpected, fixture.unexpected
                checks[mode + ('_paired' if paired else '_single')] = True
                await context.close()
            await boundary_checks(browser, clip, f'http://127.0.0.1:{server.server_port}', checks, errors)
            await browser.close()
        assert not errors, errors
        report = {'checks':checks, 'page_errors':errors, 'user_library_untouched':True}
        (OUT/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))
    finally:
        server.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
