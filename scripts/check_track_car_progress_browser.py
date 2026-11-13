"""Isolated Analysis car-marker progression checks with known synthetic laps.

The browser runs the actual frontend and Python comparison functions against
in-memory recordings. Positions validate the frontend mapping contract, not GPS
accuracy or automatically detected real-video lap boundaries. No saved session,
source video, or external API is modified.
"""
import argparse
import asyncio
import copy
import functools
import json
import math
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from check_delete_swap_browser import (
    CURRENT, REFERENCE, PROTECTED, ROOT, SOCKET_FIXTURE, StaticHandler, Fixtures,
)
from telemetry.catalog import catalog
from telemetry.comparison import compare_session_ranges, trace_session_range
from telemetry.tracks import get_track


class ProgressFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.results = []
        for sid, speed, duration, name in (
            (CURRENT, 180, 260, 'McLaren 720S GT3 Evo'),
            (REFERENCE, 150, 195, 'BMW M4 GT3'),
            (PROTECTED, 90, 60, 'Ferrari 296 GT3'),
        ):
            data = self.sessions[sid]
            prototype = data['samples'][0]
            data['samples'] = [{**prototype, 'time': index / 10, 'speed': speed}
                               for index in range(duration * 10 + 1)]
            data['session'].update(sample_count=len(data['samples']),
                context={'car_name': name, 'track_id': 'silverstone', 'track_name': 'Silverstone'})
        self.sessions[CURRENT]['laps'] = [
            {'id': index + 1, 'start_time': start, 'end_time': start + 120,
             'duration': 120, 'complete': True, 'valid': True, 'segment_id': 1,
             'source': 'timer'} for index, start in enumerate((10, 130))]
        self.sessions[REFERENCE]['session']['metadata']['reviewed_laps'] = [
            {'id': 'reviewed-1', 'start_time': 30, 'end_time': 180, 'segment_id': 1,
             'lap_number': 1, 'boundaries_verified': False, 'valid': True}]
        self.track = copy.deepcopy(get_track('silverstone'))
        # A nonzero finish offset catches assumptions that point zero is the line.
        self.finish = .07
        self.track['timing_lines'] = [
            {'kind': 'start_finish', 'label': 'Start / finish', 'progress': self.finish},
            {'kind': 'sector', 'label': 'Sector 1', 'sector': 1, 'progress': .4},
        ]

    async def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/api/catalog':
            return await route.fulfill(json=catalog())
        if path == '/api/tracks/silverstone':
            return await route.fulfill(json=self.track)
        if path.endswith('/lap-candidates'):
            sid = path.split('/')[3]
            return await route.fulfill(json={'candidates': self.sessions[sid]['laps']})
        if path.endswith('/video'):
            return await route.fulfill(json={'state': 'unavailable', 'url': None,
                                            'message': 'Telemetry-only progression fixture.'})
        if path in {'/api/compare', '/api/trace'}:
            body = request.post_data_json
            self.requests.append((request.method, path))
            if path == '/api/compare':
                current = self.sessions[body['current_session_id']]
                reference = self.sessions[body['reference_session_id']]
                result = compare_session_ranges(reference['samples'], current['samples'],
                    reference_range=body['reference_range'], current_range=body['current_range'],
                    axis=body['axis'], align_start=body['align_start'], align_end=body['align_end'],
                    alignment_mode=body['alignment_mode'], landmarks=body['landmarks'])
                result['sessions'] = {'current': current['session'], 'reference': reference['session']}
            else:
                current = self.sessions[body['session_id']]
                result = trace_session_range(current['samples'], body['range'], body['axis'])
                result['sessions'] = {'current': current['session'], 'reference': None}
            self.results.append(result)
            return await route.fulfill(json=result)
        return await super().route(route)


def preferences(axis='distance', current=CURRENT, current_range='lap-1',
                reference='', reference_range='reviewed-1', start=None, end=None):
    return {'axis': axis, 'alignment_mode': 'raw', 'align_start': False, 'align_end': False,
            'current': {'id': current, 'range': current_range, 'segment': '1',
                        **({'start': start, 'end': end} if start is not None else {})},
            'reference': {'id': reference, 'range': reference_range, 'segment': '1'}}


async def ready(page):
    await expect(page.locator('#runComparison')).to_have_attribute('aria-busy', 'false', timeout=20000)
    await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=20000)
    await expect(page.locator('#trackSelect')).to_have_value('silverstone')
    await expect(page.locator('#trackAnnotations')).to_have_attribute('data-track-id', 'silverstone')


async def seek(page, position):
    # Chromium range inputs with step=any reject Playwright.fill(decimal).
    # Set the ordinary DOM value (including its native bounds clamping), then
    # dispatch the same input event generated by dragging the review slider.
    await page.locator('#trackReviewDistance').evaluate('(input, value) => {input.value = value;}', position)
    await page.locator('#trackReviewDistance').dispatch_event('input')
    await page.evaluate('() => new Promise(resolve => requestAnimationFrame(resolve))')


async def marker(page, side='current'):
    selector = '#trackCarMarker' if side == 'current' else '#trackReferenceCarMarker'
    return await page.locator(selector).evaluate('e => ({hidden:e.hidden, '
        'progress:Number(e.dataset.progress), x:Number(e.dataset.x), y:Number(e.dataset.y), '
        'label:e.getAttribute("aria-label"), transform:e.style.transform})')


def path_point(track, fraction):
    distance = fraction % 1 * track['length_m']
    points = track['points']
    for a, b in zip(points, points[1:]):
        if a['distance_m'] <= distance <= b['distance_m']:
            width = b['distance_m'] - a['distance_m']
            ratio = (distance - a['distance_m']) / width if width else 0
            return (a['x'] + (b['x'] - a['x']) * ratio,
                    a['y'] + (b['y'] - a['y']) * ratio)
    raise AssertionError(('No point in circuit for fraction', fraction))


async def assert_progress(page, fixture, progress, side='current', tolerance=.003):
    result = await marker(page, side)
    assert not result['hidden'], (side, progress, result)
    assert math.isfinite(result['progress']) and abs(result['progress'] - progress) <= tolerance, (side, progress, result)
    x, y = path_point(fixture.track, fixture.finish + result['progress'])
    assert math.hypot(result['x'] - x, result['y'] - y) < .003, (side, result, x, y)
    return result


async def new_page(browser, url, selected, errors, fixture=None, map_preferences=None):
    fixture = fixture or ProgressFixtures()
    context = await browser.new_context(viewport={'width': 1600, 'height': 1100})
    await context.route('**/api/**', fixture.route)
    await context.add_init_script(SOCKET_FIXTURE)
    await context.add_init_script('localStorage.setItem("accc.comparison.v1", ' + json.dumps(json.dumps(selected)) + ');')
    if map_preferences is not None:
        key = f"accc.track-review.v2.{selected['current']['id']}:{selected['reference']['id']}"
        await context.add_init_script('localStorage.setItem('+json.dumps(key)+', '+json.dumps(json.dumps(map_preferences))+');')
    page = await context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    await page.goto(url)
    await page.get_by_role('button', name='Analysis', exact=True).click()
    await ready(page)
    return context, page, fixture


async def axis(page, value):
    await page.locator('#axisElapsed' if value == 'elapsed' else '#axisDistance').click()
    await ready(page)


async def select_custom(page, start, end):
    for edge, value in [('Start', start), ('End', end)]:
        await page.locator('#current' + edge).evaluate('(input, value) => {input.value = value;}', str(value))
        await page.locator('#current' + edge).dispatch_event('change')
    await ready(page)


async def check_complete(browser, url, errors, checks, output):
    context, page, fixture = await new_page(browser, url, preferences(), errors)
    try:
        await expect(page.locator('#trackProgressMode, #trackManualControls, #trackRangesAligned')).to_have_count(0)
        await expect(page.locator('#trackcurrentCar, #trackreferenceCar')).to_have_count(0)
        start = await assert_progress(page, fixture, 0)
        await seek(page, 3000)
        middle = await assert_progress(page, fixture, .5)
        assert math.hypot(middle['x'] - start['x'], middle['y'] - start['y']) > .1
        # Integrated lap is longer than nominal Silverstone; it must still finish.
        await seek(page, fixture.track['length_m'])
        await assert_progress(page, fixture, fixture.track['length_m'] / 6000)
        await seek(page, 6000)
        end = await assert_progress(page, fixture, 1)
        assert math.hypot(start['x'] - end['x'], start['y'] - end['y']) < .00001
        await expect(page.locator('#trackReferenceCarMarker')).to_be_hidden()
        checks['complete_lap_automatic_finish_and_non_nominal_length'] = {'start': start, 'middle': middle, 'end': end}

        # Real-time playback advances using the selected source clock on each axis.
        advances = {}
        for mode, initial in [('distance', 1500), ('elapsed', 30)]:
            if mode == 'elapsed':
                await axis(page, mode)
            await seek(page, initial)
            before = await assert_progress(page, fixture, .25)
            await page.locator('#trackReviewPlay').click()
            await page.wait_for_timeout(1100)
            await page.locator('#trackReviewPlay').click()
            after = await marker(page)
            elapsed = (after['progress'] - before['progress']) * 120
            assert .6 < elapsed < 1.8, (mode, before, after, elapsed)
            await page.wait_for_timeout(300)
            paused = await marker(page)
            assert abs(paused['progress'] - after['progress']) < .0001, (after, paused)
            advances[mode] = {'source_seconds_advanced': elapsed, 'paused_progress': paused['progress']}
        checks['play_and_pause_in_distance_and_time'] = advances

        await seek(page, 119.7)
        await page.locator('#trackReviewPlay').click()
        await page.wait_for_timeout(700)
        await assert_progress(page, fixture, 1)
        await expect(page.locator('#trackReviewPlay')).to_have_attribute('aria-pressed', 'false')
        await page.locator('#trackReviewPlay').click()
        await page.wait_for_timeout(160)
        restarted = await marker(page)
        assert 0 <= restarted['progress'] < .02, restarted
        await page.locator('#trackReviewPlay').click()
        checks['playback_stops_at_finish_and_restarts_at_start'] = restarted

        await seek(page, 60)
        await expect(page.locator('#trackcurrentImage')).to_be_visible(timeout=5000)
        await page.screenshot(path=str(output / 'complete-lap-middle.png'), full_page=True)
        assert not fixture.unexpected, fixture.unexpected
    finally:
        await context.close()


async def check_pair(browser, url, errors, checks, output):
    context, page, fixture = await new_page(browser, url,
        preferences(reference=REFERENCE), errors)
    try:
        await assert_progress(page, fixture, 0)
        await assert_progress(page, fixture, 0, 'reference')
        await seek(page, 3000)
        current = await assert_progress(page, fixture, .5)
        reference = await assert_progress(page, fixture, .48, 'reference')
        assert current['label'].startswith('Your run\n') and 'McLaren' not in current['label'], current
        assert reference['label'].startswith('Reference\n') and 'BMW' not in reference['label'], reference
        assert math.hypot(current['x'] - reference['x'], current['y'] - reference['y']) > .003
        checks['independent_run_distance_totals_and_labels'] = {'current': current, 'reference': reference}
        await seek(page, 6000)
        await assert_progress(page, fixture, 1)
        await assert_progress(page, fixture, .96, 'reference')
        await seek(page, 6250)
        await assert_progress(page, fixture, 1)
        await assert_progress(page, fixture, 1, 'reference')
        await axis(page, 'elapsed')
        await seek(page, 60)
        await assert_progress(page, fixture, .5)
        await assert_progress(page, fixture, .4, 'reference')
        checks['each_car_uses_own_duration_on_time_axis'] = True
        await seek(page, 135)
        finished = await assert_progress(page, fixture, 1)
        assert 'Lap finished' in finished['label'], finished
        await assert_progress(page, fixture, .9, 'reference')
        # Once the first car finishes, playback must continue through reference tail.
        await page.locator('#trackReviewPlay').click()
        await page.wait_for_timeout(1100)
        await page.locator('#trackReviewPlay').click()
        await assert_progress(page, fixture, 1)
        tail = await marker(page, 'reference')
        assert .904 < tail['progress'] < .92, tail
        checks['completed_car_holds_finish_while_reference_continues'] = tail
        await expect(page.locator('#trackreferenceImage')).to_be_visible(timeout=5000)
        await page.screenshot(path=str(output / 'paired-different-durations.png'), full_page=True)

        paused_changes = []
        for change in ('scrub', 'axis', 'tab', 'swap', 'selection'):
            await page.locator('#trackReviewPlay').click()
            await expect(page.locator('#trackReviewPlay')).to_have_attribute('aria-pressed', 'true')
            if change == 'scrub':
                await seek(page, 60)
            elif change == 'axis':
                await axis(page, 'distance')
            elif change == 'tab':
                await page.get_by_role('button', name='Credits', exact=True).click()
                await page.get_by_role('button', name='Analysis', exact=True).click()
            elif change == 'swap':
                await page.locator('#swapComparisonRuns').click()
                await ready(page)
            else:
                await page.locator('#currentRange').evaluate("el => {el.value = 'full'; el.dispatchEvent(new Event('change'));}")
                await ready(page)
            await expect(page.locator('#trackReviewPlay')).to_have_attribute('aria-pressed', 'false')
            before = await page.locator('#trackReviewDistance').input_value()
            await page.wait_for_timeout(180)
            assert before == await page.locator('#trackReviewDistance').input_value(), change
            paused_changes.append(change)
        checks['scrub_axis_tab_swap_and_selection_changes_pause_playback'] = paused_changes

        # Restore roles after the swap before testing a standalone reference.
        await page.locator('#swapComparisonRuns').click()
        await ready(page)
        await axis(page, 'elapsed')
        await page.locator('#referenceRange').evaluate("el => {el.value = 'reviewed-1'; el.dispatchEvent(new Event('change'));}")
        await ready(page)

        await page.locator('#currentSession').select_option('')
        await ready(page)
        await expect(page.locator('#trackCarMarker')).to_be_hidden()
        await assert_progress(page, fixture, 0, 'reference')
        await seek(page, 75)
        await assert_progress(page, fixture, .5, 'reference')
        checks['reference_only_keeps_reference_marker'] = True
        assert not fixture.unexpected, fixture.unexpected
    finally:
        await context.close()


async def check_variable_speed(browser, url, errors, checks):
    fixture = ProgressFixtures()
    for sample in fixture.sessions[CURRENT]['samples']:
        sample['speed'] = 90 + 1.5 * min(120, max(0, sample['time'] - 10))
    context, page, fixture = await new_page(browser, url,
        preferences(axis='elapsed'), errors, fixture)
    try:
        # Linear acceleration 90→270 km/h over 120 s covers 6000 m. The first
        # 60 s covers 2250 m, so distance progress is 37.5%, not 50% elapsed time.
        await seek(page, 60)
        middle = await assert_progress(page, fixture, .375)
        await seek(page, 120)
        await assert_progress(page, fixture, 1)
        await select_custom(page, 40, 70)
        # Lap-relative elapsed 30→60 s gives 937.5→2250 m of 6000 m.
        await assert_progress(page, fixture, .15625)
        await seek(page, 30)
        await assert_progress(page, fixture, .375)
        checks['variable_speed_uses_distance_for_lap_and_known_partial'] = middle
        assert not fixture.unexpected, fixture.unexpected
    finally:
        await context.close()


async def check_partial(browser, url, errors, checks):
    context, page, fixture = await new_page(browser, url,
        preferences(axis='elapsed', current_range='custom', start=40, end=70), errors)
    try:
        await assert_progress(page, fixture, .25)
        await seek(page, 15)
        await assert_progress(page, fixture, .375)
        await seek(page, 30)
        await assert_progress(page, fixture, .5)
        checks['known_lap_partial_slice_preserves_original_progress'] = True
        await page.locator('#currentRange').evaluate("el => {el.value = 'full'; el.dispatchEvent(new Event('change'));}")
        await ready(page)
        await expect(page.locator('#trackCarMarker')).to_be_hidden()
        for time, progress in [(10, 0), (70, .5), (130, 0), (190, .5), (250, 1)]:
            await seek(page, time)
            # A shared finish crossing may belong to the previous or next lap.
            if time == 130:
                value = await marker(page)
                assert not value['hidden'] and min(abs(value['progress']), abs(value['progress'] - 1)) < .003, value
                x, y = path_point(fixture.track, fixture.finish)
                assert math.hypot(value['x'] - x, value['y'] - y) < .003, value
            else:
                await assert_progress(page, fixture, progress)
        await seek(page, 259)
        await expect(page.locator('#trackCarMarker')).to_be_hidden()
        checks['full_recording_wraps_known_laps_without_locating_intro_or_outro'] = True
        assert not fixture.unexpected, fixture.unexpected
    finally:
        await context.close()

    context, page, fixture = await new_page(browser, url,
        preferences(axis='elapsed', current=PROTECTED, current_range='custom', start=5, end=35), errors,
        map_preferences={'track':'silverstone','progressMode':'manual','origin':.42,'reverse':True,'aligned':True})
    try:
        await expect(page.locator('#trackCarMarker')).to_be_hidden()
        await seek(page, 15)
        await expect(page.locator('#trackCarMarker')).to_be_hidden()
        await expect(page.locator('#trackReviewStatus')).to_contain_text('lap')
        await expect(page.locator('#trackProgressMode, #trackManualControls, #trackSetOrigin, #trackReverse')).to_have_count(0)
        checks['legacy_manual_mode_ignored_and_unknown_partial_not_fabricated'] = True
        # Explicit lap-boundary locks replace the former force-one-lap dropdown.
        for edge, time in [('Start', 5), ('End', 35)]:
            await page.locator('#current' + edge + 'Boundary').click()
            await page.locator('#alignmentTime').fill(str(time))
            await page.locator('#alignmentTime').dispatch_event('input')
            await expect(page.locator('#alignmentLock')).to_be_enabled()
            await page.locator('#alignmentLock').click()
            await ready(page)
        await seek(page, 15)
        await assert_progress(page, fixture, .5)
        await seek(page, 0)
        await assert_progress(page, fixture, 0)
        await seek(page, 30)
        await assert_progress(page, fixture, 1)
        checks['explicit_lap_boundary_locks_enable_automatic_progress'] = True
        assert not fixture.unexpected, fixture.unexpected
    finally:
        await context.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/track-car-progress')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks, passed = [], {}, False
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            url = f'http://127.0.0.1:{server.server_port}'
            await check_complete(browser, url, errors, checks, args.output)
            await check_pair(browser, url, errors, checks, args.output)
            await check_partial(browser, url, errors, checks)
            await check_variable_speed(browser, url, errors, checks)
            assert not errors, errors
            passed = True
            await browser.close()
    finally:
        server.shutdown()
        report = {'passed': passed,
                  'scope': 'Synthetic known-lap fixtures, real frontend and comparison backend; not measured racing-line accuracy.',
                  'checks': checks, 'page_errors': errors}
        (args.output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
