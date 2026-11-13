"""Native boundary-picker races with generated video and an isolated profile."""
import argparse
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from check_missing_hud_browser import MissingFixtures, make_clip
from check_delete_swap_browser import CURRENT
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler


class DelayedVideoFixtures(MissingFixtures):
    def __init__(self, clip):
        super().__init__(clip, 'complete')
        self.gate = None
        self.arrived = asyncio.Event()
        self.sessions[CURRENT]['session']['metadata'].update(
            reviewed_laps=[{'id': 'boundary-fixture', 'start_time': 1.6, 'end_time': 6.4,
                            'segment_id': 1, 'boundaries_verified': False}],
            comparison_defaults={'range_id': 'boundary-fixture'})

    async def route(self, route):
        if urlparse(route.request.url).path.endswith('/video/media') and self.gate:
            gate = self.gate
            self.arrived.set()
            await gate.wait()
        await super().route(route)


async def current_video(page, time):
    await expect(page.locator('#alignmentVideo')).to_be_visible()
    await page.wait_for_function('''target => {
      const video = document.querySelector('#alignmentVideo');
      return video.readyState >= 2 && !video.seeking && Math.abs(video.currentTime - target) < .005;
    }''', arg=time)
    await expect(page.locator('#alignmentTime')).to_have_value(f'{time:.3f}')
    await expect(page.locator('#alignmentLock')).to_be_enabled()


async def run(args):
    args.output.mkdir(exist_ok=True, parents=True)
    clip = args.output / 'generated-video.mp4'
    make_clip(clip)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fixture, checks, errors, overrides = DelayedVideoFixtures(clip), {}, [], []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport={'width': 1440, 'height': 1100})
            await context.route('**/api/**', fixture.route)
            if args.comparison_script:
                async def override_script(route):
                    overrides.append(route.request.url)
                    await route.fulfill(path=str(args.comparison_script), content_type='application/javascript')
                await context.route('**/static/comparison.js*', override_script)
            await context.add_init_script(SOCKET_FIXTURE)
            selected = {'axis': 'elapsed', 'current': {'id': CURRENT, 'range': 'boundary-fixture'}, 'reference': {'id': ''}}
            await context.add_init_script('localStorage.setItem("accc.comparison.v1",' + json.dumps(json.dumps(selected)) + ');')
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            if args.comparison_script:
                assert len(overrides) == 1, overrides
            await page.get_by_role('button', name='Analysis', exact=True).click()
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=20000)
            await page.wait_for_function('document.querySelector("#trackcurrentNative").readyState >= 2')

            fixture.gate = asyncio.Event()
            await page.locator('#currentStartBoundary').click()
            await asyncio.wait_for(fixture.arrived.wait(), 5)
            await page.locator('#alignmentTime').fill('1.15')
            await expect(page.locator('#alignmentTime')).to_have_value('1.150')
            fixture.gate.set()
            fixture.gate = None
            if args.expect_stale:
                await current_video(page, 1.6)
                await page.locator('#alignmentLock').click()
                await expect(page.locator('#currentStart')).to_have_value('1.600')
                checks['old_metadata_handler_reproduces_stale_boundary'] = True
            else:
                await current_video(page, 1.15)
                await page.locator('#alignmentLock').click()
                await expect(page.locator('#currentStart')).to_have_value('1.150')
                checks['typed_time_before_native_metadata_is_preserved'] = True

                await page.locator('#currentEndBoundary').click()
                await current_video(page, 6.4)
                await page.locator('#alignmentTime').fill('6.2')
                await page.locator('#alignmentTime').fill('5.8')
                await current_video(page, 5.8)
                await page.locator('#alignmentLock').click()
                await expect(page.locator('#currentEnd')).to_have_value('5.800')
                checks['successive_native_seeks_lock_latest_requested_frame'] = True

                await page.locator('#currentEndBoundary').click()
                await current_video(page, 5.8)
                await page.locator('#alignmentTime').fill('6.1')
                await current_video(page, 6.1)
                await page.locator('#closeAlignment').click()
                await expect(page.locator('#currentEnd')).to_have_value('5.800')
                await page.locator('#currentStartBoundary').click()
                await current_video(page, 1.15)
                await page.locator('#closeAlignment').click()
                await page.locator('#currentEndBoundary').click()
                await current_video(page, 5.8)
                checks['cancel_and_reopen_do_not_commit_or_leak_prior_boundary'] = True

                await page.locator('#alignmentTime').fill('5.4')
                await current_video(page, 5.4)
                await page.locator('#alignmentVideo').evaluate('(video) => video.play()')
                await page.wait_for_function('document.querySelector("#alignmentVideo").currentTime > 5.55')
                # Native timeupdate is coarser than frame presentation. Freeze its
                # visible input at an older stamp to exercise the real video clock.
                observed = await page.evaluate('''() => {
                    const video = document.querySelector('#alignmentVideo');
                    video.ontimeupdate = null;
                    document.querySelector('#alignmentTime').value = '5.400';
                    const actual = video.currentTime;
                    document.querySelector('#alignmentLock').click();
                    return {actual, saved: Number(document.querySelector('#currentEnd').value), paused: video.paused};
                }''')
                assert observed['actual'] > 5.55 and abs(observed['saved'] - observed['actual']) < .003, observed
                assert observed['paused']
                checks['playing_lock_uses_paused_video_clock_not_stale_input'] = observed
            assert not errors, errors
            await browser.close()
    finally:
        if fixture.gate:
            fixture.gate.set()
        server.shutdown()
        server.server_close()
    report = {'checks': checks, 'page_errors': errors, 'real_library_mutated': False}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'boundary-video')
    parser.add_argument('--comparison-script', type=Path)
    parser.add_argument('--expect-stale', action='store_true')
    asyncio.run(run(parser.parse_args()))
