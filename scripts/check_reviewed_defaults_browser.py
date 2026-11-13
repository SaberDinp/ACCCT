"""Isolated reviewed-lap defaults, lock persistence and revision migration checks."""
import argparse
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from check_delete_swap_browser import CURRENT, REFERENCE, Fixtures
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler


class ReviewedFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.inference_requests = []
        self.sessions = {key: self.sessions[key] for key in (CURRENT, REFERENCE)}
        for sid, start in ((CURRENT, 10), (REFERENCE, 12)):
            data = self.sessions[sid]
            data['session']['metadata'].update(
                telemetry_context={'track_id': 'silverstone'},
                reviewed_laps=[{'id': 'reviewed-1', 'lap_number': 1,
                    'start_time': start, 'end_time': 110, 'segment_id': 1,
                    'valid': None, 'boundaries_verified': True,
                    'start_marker': 'fixture-sf', 'end_marker': 'fixture-sf'}],
                comparison_defaults={'range_id': 'reviewed-1', 'revision': 'review-v1',
                                     'alignment_mode': 'lap_endpoints'})

    async def route(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/track-inference':
            self.inference_requests.append(route.request.post_data_json)
        if path.endswith('/video/media'):
            return await route.fulfill(status=404)
        if path.endswith('/video'):
            return await route.fulfill(json={'state': 'unavailable', 'url': None})
        return await super().route(route)


async def settled(page):
    await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=20000)
    await expect(page.locator('#runComparison')).to_have_attribute('aria-busy', 'false')


async def opened(browser, url, fixture, errors, saved=None, width=1440):
    context = await browser.new_context(viewport={'width': width, 'height': 1000})
    await context.route('**/api/**', fixture.route)
    await context.add_init_script(SOCKET_FIXTURE)
    if saved:
        await context.add_init_script('''if (!sessionStorage.getItem('reviewed-seed')) {
            localStorage.setItem('accc.comparison.v1', %s);
            sessionStorage.setItem('reviewed-seed', '1');
        }''' % json.dumps(json.dumps(saved)))
    page = await context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    await page.goto(url)
    await page.get_by_role('button', name='Analysis', exact=True).click()
    await settled(page)
    return context, page


async def run(args):
    args.output.mkdir(exist_ok=True, parents=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fixture, checks, errors = ReviewedFixtures(), {}, []
    stale = {'axis': 'elapsed', 'alignment_mode': 'landmarks', 'align_start': True, 'align_end': True,
        'landmarks': [{'current_time': 15, 'reference_time': 16}],
        'current': {'id': 'deleted-current', 'range': 'full'},
        'reference': {'id': 'deleted-reference', 'range': 'custom', 'start': 1, 'end': 155}}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            url = f'http://127.0.0.1:{server.server_port}'
            context, page = await opened(browser, url, fixture, errors, stale)
            for side, start in (('current', '10.000'), ('reference', '12.000')):
                await expect(page.locator(f'#{side}Range')).to_have_value('reviewed-1')
                await expect(page.locator(f'#{side}Start')).to_have_value(start)
            request = fixture.comparisons[-1]
            assert request['align_start'] and request['align_end']
            assert request['alignment_mode'] == 'raw' and request['landmarks'] == []
            await expect(page.locator('#comparisonAlignmentMode, #confirmAlignment, #confirmFinishAlignment, #landmarkRows, #currentSaveLapRange')).to_have_count(0)
            checks['stale_ids_use_verified_defaults_without_restoring_removed_registration'] = True

            await page.locator('#currentEndBoundary').click()
            await expect(page.locator('#alignmentDialog')).to_be_visible()
            await page.locator('#alignmentTime').fill('111')
            await expect(page.locator('#alignmentLock')).to_be_enabled(timeout=20000)
            await page.locator('#alignmentLock').click()
            await settled(page)
            await expect(page.locator('#currentEndBoundaryTime')).to_contain_text('Locked')
            assert not fixture.comparisons[-1]['align_start'] and not fixture.comparisons[-1]['align_end']
            await page.reload()
            await page.get_by_role('button', name='Analysis', exact=True).click()
            await settled(page)
            await expect(page.locator('#currentRange')).to_have_value('custom')
            await expect(page.locator('#currentEnd')).to_have_value('111.000')
            await expect(page.locator('#currentEndBoundaryTime')).to_contain_text('Locked')
            checks['unchanged_default_revision_preserves_custom_boundary_and_lock'] = True

            current = fixture.sessions[CURRENT]['session']['metadata']
            current['comparison_defaults']['revision'] = 'review-v2'
            current['reviewed_laps'][0].update(start_time=11, end_time=111)
            await page.locator('#refreshComparisons').click()
            await expect(page.locator('#currentRange')).to_have_value('reviewed-1', timeout=20000)
            await settled(page)
            await expect(page.locator('#currentStart')).to_have_value('11.000')
            await expect(page.locator('#currentEndBoundaryTime')).not_to_contain_text('Locked')
            assert fixture.comparisons[-1]['align_start'] and fixture.comparisons[-1]['align_end']
            checks['new_default_revision_replaces_old_custom_range_and_lock'] = True
            await context.close()

            context, page = await opened(browser, url, fixture, errors, width=390)
            await expect(page.locator('#currentStart')).to_have_value('11.000')
            assert fixture.comparisons[-1]['align_start'] and fixture.comparisons[-1]['alignment_mode'] == 'raw'
            assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
            await page.screenshot(path=str(args.output / 'reviewed-defaults-mobile.png'), full_page=True)
            checks['fresh_profile_uses_current_reviewed_defaults_and_mobile_fits'] = True

            reference = fixture.sessions[REFERENCE]['session']['metadata']
            reference['reviewed_laps'][0]['start_marker'] = 'other-sf'
            reference['comparison_defaults']['revision'] = 'mismatched-v2'
            await page.locator('#refreshComparisons').click()
            await expect(page.locator('#lapBoundaryHint')).to_be_visible(timeout=20000)
            await settled(page)
            assert not fixture.comparisons[-1]['align_start'] and fixture.comparisons[-1]['align_end']
            checks['mismatched_reviewed_start_markers_never_auto_align_start'] = True

            reference['reviewed_laps'][0]['start_marker'] = 'fixture-sf'
            reference['reviewed_laps'][0]['landmarks'] = [{'marker_id': 'bridge', 'time': 58, 'label': 'Bridge'}]
            current['reviewed_laps'][0]['landmarks'] = [{'marker_id': 'bridge', 'time': 60, 'label': 'Bridge'}]
            reference['comparison_defaults']['revision'] = 'matched-v3'
            await page.locator('#refreshComparisons').click()
            await expect(page.locator('#lapBoundaryHint')).to_be_hidden(timeout=20000)
            await settled(page)
            request = fixture.comparisons[-1]
            assert request['align_start'] and request['align_end']
            assert request['alignment_mode'] == 'raw' and request['landmarks'] == []
            checks['matching_markers_realign_without_loading_legacy_landmarks'] = True
            await context.close()

            # A custom selection from an earlier version can be restricted to
            # one source segment. A rewind creates overlapping timestamps in
            # another segment, which must not enter the restored trace/inference.
            current.pop('telemetry_context')
            data = fixture.sessions[CURRENT]
            data['samples'] += [{**sample, 'segment_id': 2, 'speed': 250} for sample in data['samples']]
            saved = {'axis': 'distance', 'current': {'id': CURRENT, 'range': 'custom',
                'start': '10.000', 'end': '110.000', 'segment': '1',
                'boundary_locks': {'start': 10, 'end': 110}, 'defaults_revision': 'review-v2'},
                'reference': {'id': ''}}
            fixture.inference_requests.clear()
            context, page = await opened(browser, url, fixture, errors, saved)
            await expect(page.locator('#trackDetectionStatus')).to_contain_text('No confident circuit match', timeout=20000)
            request = fixture.inference_requests[-1]
            assert request['range'] == {'start_time': 10, 'end_time': 110, 'segment_id': 1}, request
            assert request['range_is_lap'] is True
            restored = json.loads(await page.evaluate('localStorage.getItem("accc.comparison.v1")'))
            assert restored['current']['segment'] == '1'
            checks['restored_custom_segment_filter_reaches_trace_and_inference_after_rewind'] = True
            assert not errors, errors
            assert not fixture.unexpected, fixture.unexpected
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    result = {'checks': checks, 'real_library_mutated': False, 'page_errors': errors}
    (args.output / 'reviewed-defaults-browser-checks.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'reviewed-defaults')
    asyncio.run(run(parser.parse_args()))
