"""Independent browser regression for local section pace coloring.

Only static frontend assets are served. All API/WebSocket calls use in-memory
synthetic recordings; no user session, media, or external service is modified.
This checks local section time gains and rendered pixels, not GPS accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from check_track_car_progress_browser import (
    CURRENT, REFERENCE, ROOT, StaticHandler, ProgressFixtures,
    axis, new_page, path_point, preferences, ready, seek,
)

CYAN = [109, 204, 223]
GOLD = [224, 191, 121]
EQUAL = [183, 196, 189]
MISSING = [77, 94, 84]
CURRENT_TIMES = [20, 40, 20, 40]
REFERENCE_TIMES = [30, 35, 30, 35]
PROBES = [.12, .37, .62, .87]


class PaceFixtures(ProgressFixtures):
    def __init__(self, mode='alternating'):
        super().__init__()
        self.mode = mode
        reference_times = CURRENT_TIMES if mode == 'equal' else REFERENCE_TIMES
        for sid, start, section_times in ((CURRENT, 10, CURRENT_TIMES), (REFERENCE, 30, reference_times)):
            data = self.sessions[sid]
            prototype = data['samples'][0]
            boundaries = [0]
            for duration in section_times:
                boundaries.append(boundaries[-1] + duration)
            duration = boundaries[-1]
            samples = []
            for index in range(round((start + duration + 5) * 10) + 1):
                timestamp = index / 10
                local = timestamp - start
                section = next((i for i in range(4) if local < boundaries[i + 1]), 3)
                # Each quarter covers1500m. Trapezoidal sampling only perturbs
                # the transition edges; probes are well inside every section.
                samples.append({**prototype, 'time': timestamp,
                                'speed': 1500 / section_times[section] * 3.6})
            data['samples'] = samples
            data['session']['sample_count'] = len(samples)
            if sid == CURRENT:
                data['laps'] = [{'id': 1, 'start_time': start, 'end_time': start + duration,
                                 'duration': duration, 'complete': True, 'valid': True,
                                 'segment_id': 1, 'source': 'timer'}]
            else:
                data['session']['metadata']['reviewed_laps'] = [
                    {'id': 'reviewed-1', 'start_time': start, 'end_time': start + duration,
                     'segment_id': 1, 'lap_number': 1, 'boundaries_verified': False, 'valid': True}]

    async def route(self, route):
        if self.mode != 'gapped' or urlparse(route.request.url).path != '/api/compare':
            return await super().route(route)

        # Keep a known lap geometry/distance model, but omit one interval from
        # the delivered comparison observations. This independently exercises
        # UI passage construction across a missing response interval.
        class GappedResponse:
            request = route.request

            async def fulfill(self, **kwargs):
                result = kwargs['json']
                total = result['current']['raw_distance']
                for point in result['points']:
                    sample = point.get('current')
                    if not sample:
                        continue
                    progress = sample['raw_distance'] / total
                    if .27 < progress < .46:
                        point['current'] = None
                    elif progress >= .46:
                        sample['distance_segment_id'] = 'after-fixture-gap'
                return await route.fulfill(**kwargs)

        return await super().route(GappedResponse())


def passages(section_times, segment=1):
    result, elapsed = [{'progress': 0, 'time': 0, 'segment': segment}], 0
    for index, duration in enumerate(section_times):
        elapsed += duration
        result.append({'progress': (index + 1) / len(section_times), 'time': elapsed, 'segment': segment})
    return result


async def helper_build(page, current, reference, **options):
    return await page.evaluate('([current, reference, options]) => window.ACCCTrackPace.build(current, reference, options)',
                               [current, reference, options])


async def check_helper(page, checks):
    await page.wait_for_function('Boolean(window.ACCCTrackPace?.build)', timeout=10000)
    current, reference = passages(CURRENT_TIMES), passages(REFERENCE_TIMES)
    bins = await helper_build(page, current, reference, bins=4, threshold=.01)
    assert [item['winner'] for item in bins] == ['current', 'reference', 'current', 'reference'], bins
    assert [item['gain'] for item in bins] == [10, -5, 10, -5], bins
    assert [item['currentTime'] for item in bins] == CURRENT_TIMES, bins
    assert [item['referenceTime'] for item in bins] == REFERENCE_TIMES, bins
    assert [(item['start'], item['end']) for item in bins] == [(0, .25), (.25, .5), (.5, .75), (.75, 1)], bins
    cumulative = [r['time'] - c['time'] for c, r in zip(current[1:], reference[1:])]
    assert all(value > 0 for value in cumulative)
    swapped = await helper_build(page, reference, current, bins=4, threshold=.01)
    assert [item['winner'] for item in swapped] == ['reference', 'current', 'reference', 'current']
    assert [item['gain'] for item in swapped] == [-item['gain'] for item in bins]
    checks['local_gains_alternate_despite_current_cumulative_lead'] = {
        'bins': bins, 'cumulative_current_lead_seconds': cumulative, 'swapped_bins': swapped}

    near_equal = await helper_build(page, passages([10] * 4), passages([10.005] * 4), bins=4, threshold=.01)
    assert all(item['winner'] == 'equal' for item in near_equal), near_equal
    empty = await helper_build(page, current, [], bins=4, threshold=.01)
    assert len(empty) == 4 and all(item['winner'] == 'unavailable' for item in empty), empty
    checks['equal_threshold_and_missing_reference'] = {'near_equal': near_equal, 'missing': empty}

    # One continuous reference, but the current run is missing20–50% of its lap.
    # Its changed segment forbids interpolating across the unobserved interval.
    gapped = [{'progress': value / 10, 'time': value * 10, 'segment': 1 if value <= 2 else 2}
              for value in [0, 1, 2, 5, 6, 7, 8, 9, 10]]
    complete = [{'progress': value / 10, 'time': value * 11, 'segment': 1} for value in range(11)]
    missing = await helper_build(page, gapped, complete, bins=10, threshold=.01)
    assert [item['winner'] for item in missing[2:5]] == ['unavailable'] * 3, missing
    assert all(item['winner'] == 'current' for item in missing[:2] + missing[5:]), missing
    assert all(item['gain'] is None for item in missing[2:5]), missing
    checks['discontinuity_gap_is_not_bridged'] = missing


async def pace_counts(page):
    return await page.locator('#trackMap').evaluate('canvas => ({current:Number(canvas.dataset.paceCurrent), '
        'reference:Number(canvas.dataset.paceReference), equal:Number(canvas.dataset.paceEqual), '
        'unavailable:Number(canvas.dataset.paceUnavailable), builds:Number(canvas.dataset.paceBuilds)})')


async def canvas_colors(page):
    return await page.locator('#trackMap').evaluate('''canvas => {
        const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        const counts = {current:0, reference:0};
        for (let i=0;i<data.length;i+=4) {
            if (data[i+3] < 220) continue;
            for (const [name, rgb] of [['current',[109,204,223]], ['reference',[224,191,121]]])
                if (rgb.every((value,k) => Math.abs(data[i+k]-value) <= 8)) counts[name]++;
        }
        return counts;
    }''')


async def pixel_probe(page, fixture, progress, expected, reverse=False):
    x, y = path_point(fixture.track, fixture.finish + (-progress if reverse else progress))
    result = await page.locator('#trackMap').evaluate('''(canvas, value) => {
        const [x,y,expected] = value, box=canvas.getBoundingClientRect();
        const size=Math.min(box.width,box.height)-40;
        const px=(box.width-size)/2+x*size, py=(box.height-size)/2+y*size;
        const ratio=canvas.width/box.width, radius=6;
        const left=Math.max(0,Math.round(px*ratio)-radius), top=Math.max(0,Math.round(py*ratio)-radius);
        const data=canvas.getContext('2d').getImageData(left,top,radius*2+1,radius*2+1).data;
        let count=0, closest=Infinity;
        for(let i=0;i<data.length;i+=4) {
            if(data[i+3]<220) continue;
            const difference=Math.max(...expected.map((v,k)=>Math.abs(v-data[i+k])));
            closest=Math.min(closest,difference); if(difference<=8) count++;
        }
        return {matches:count,closest,clientX:box.left+px,clientY:box.top+py};
    }''', [x, y, expected])
    assert result['matches'] >= 3, (progress, expected, result)
    return result


async def wait_overlay(page):
    await expect(page.locator('#trackPaceLegend')).to_be_visible()
    await page.wait_for_function('Number(document.querySelector("#trackMap").dataset.paceCurrent)>15 && '
                                 'Number(document.querySelector("#trackMap").dataset.paceReference)>15')


async def assert_quarters(page, fixture, swapped=False):
    expected = [CYAN, GOLD, CYAN, GOLD] if not swapped else [GOLD, CYAN, GOLD, CYAN]
    return [await pixel_probe(page, fixture, progress, color) for progress, color in zip(PROBES, expected)]


async def check_overlay(browser, url, errors, checks, output):
    fixture = PaceFixtures()
    context, page, fixture = await new_page(browser, url, preferences(reference=REFERENCE), errors, fixture)
    try:
        await check_helper(page, checks)
        await wait_overlay(page)
        await page.locator('#trackMap').scroll_into_view_if_needed()
        results = {}
        for mode in ['distance', 'elapsed']:
            if mode == 'elapsed':
                await axis(page, mode)
                await wait_overlay(page)
            pixels = await assert_quarters(page, fixture)
            counts = await pace_counts(page)
            palette = await canvas_colors(page)
            assert palette['current'] > 50 and palette['reference'] > 50, palette
            assert counts['current'] > 15 and counts['reference'] > 15, counts
            results[mode] = {'bin_counts': counts, 'actual_color_pixels': palette, 'quarter_probes': pixels}
        checks['both_axes_draw_alternating_local_colors'] = results

        # Hover two oppositely colored sections. This must describe local time
        # gain, not merely the cumulative leader or a generic corner label.
        hovers = []
        for progress, color, label in [(PROBES[0], CYAN, 'Your run'), (PROBES[1], GOLD, 'Reference')]:
            point = await pixel_probe(page, fixture, progress, color)
            await page.mouse.move(point['clientX'], point['clientY'])
            await expect(page.locator('#trackPaceTip')).to_be_visible()
            text = await page.locator('#trackPaceTip').inner_text()
            assert label in text and 'gains' in text and 'here' in text and '%' in text, text
            assert ' s' in text, text
            hovers.append(text)
        checks['map_hover_reports_local_gain_and_section_bounds'] = hovers
        await page.screenshot(path=str(output / 'paired-local-pace.png'), full_page=True)

        before = await pace_counts(page)
        for time in [5, 15, 35, 65, 85]:
            await seek(page, time)
        after = await pace_counts(page)
        assert after['builds'] == before['builds'], (before, after)
        checks['scrubbing_reuses_pace_bins'] = {'before': before, 'after': after}

        await page.locator('#swapComparisonRuns').click()
        await ready(page)
        await wait_overlay(page)
        swapped = await assert_quarters(page, fixture, swapped=True)
        checks['swapping_runs_reverses_actual_map_colors'] = swapped
        await page.screenshot(path=str(output / 'swapped-local-pace.png'), full_page=True)

        await page.locator('#referenceSession').select_option('')
        await ready(page)
        await expect(page.locator('#trackPaceLegend')).to_be_hidden()
        await expect(page.locator('#trackPaceTip')).to_be_hidden()
        palette = await canvas_colors(page)
        counts = await pace_counts(page)
        assert palette == {'current': 0, 'reference': 0}, palette
        assert counts['current'] == counts['reference'] == 0, counts
        checks['single_recording_clears_overlay_legend_and_tooltip'] = {'palette': palette, 'counts': counts}
        await page.screenshot(path=str(output / 'single-recording-no-pace.png'), full_page=True)
        assert not fixture.unexpected, fixture.unexpected
    finally:
        await context.close()


async def check_neutral_colors(browser, url, errors, checks, output):
    for mode, winner, color in [('equal', 'equal', EQUAL), ('gapped', 'unavailable', MISSING)]:
        fixture = PaceFixtures(mode)
        context, page, fixture = await new_page(browser, url, preferences(reference=REFERENCE), errors, fixture)
        try:
            await expect(page.locator('#trackPaceLegend')).to_be_visible()
            await page.wait_for_function('([winner]) => Number(document.querySelector("#trackMap").dataset["pace"+winner[0].toUpperCase()+winner.slice(1)])>15', arg=[winner])
            await page.locator('#trackMap').scroll_into_view_if_needed()
            counts = await pace_counts(page)
            if mode == 'equal':
                assert counts['equal'] == 120 and counts['current'] == counts['reference'] == 0, counts
                probes = [await pixel_probe(page, fixture, progress, color) for progress in PROBES]
                assert await canvas_colors(page) == {'current': 0, 'reference': 0}
            else:
                assert counts['unavailable'] >= 20 and counts['current'] > 15 and counts['reference'] > 15, counts
                probes = [await pixel_probe(page, fixture, .37, color),
                          await pixel_probe(page, fixture, .12, CYAN),
                          await pixel_probe(page, fixture, .87, GOLD)]
            point = probes[0]
            await page.mouse.move(point['clientX'], point['clientY'])
            await expect(page.locator('#trackPaceTip')).to_be_visible()
            await expect(page.locator('#trackPaceTip')).to_have_attribute('data-winner', winner)
            tip = await page.locator('#trackPaceTip').inner_text()
            assert ('Even pace' if mode == 'equal' else 'No comparison') in tip, tip
            checks[f'{mode}_sections_render_neutral_pixels_and_hover'] = {'counts': counts, 'pixel_probes': probes, 'tooltip': tip}
            await page.screenshot(path=str(output / f'{mode}-sections.png'), full_page=True)
            assert not fixture.unexpected, fixture.unexpected
        finally:
            await context.close()


async def check_automatic_alignment(browser, url, errors, checks, output):
    fixture = PaceFixtures()
    context, page, fixture = await new_page(browser, url, preferences(axis='elapsed', reference=REFERENCE), errors, fixture,
        map_preferences={'track':'silverstone','progressMode':'manual','origin':.31,'reverse':True,'aligned':True})
    try:
        await wait_overlay(page)
        await expect(page.locator('#trackProgressMode, #trackSetOrigin, #trackRangesAligned, #trackReverse')).to_have_count(0)
        await page.locator('#trackMap').scroll_into_view_if_needed()
        forward = await assert_quarters(page, fixture)
        before = await pace_counts(page)
        point = forward[1]
        await page.mouse.move(point['clientX'], point['clientY'])
        await expect(page.locator('#trackPaceTip')).to_be_visible()
        await expect(page.locator('#trackPaceTip')).to_have_attribute('data-winner', 'reference')
        checks['legacy_manual_origin_and_reverse_cannot_change_automatic_lap_colors'] = {
            'forward_probes': forward, 'counts': before,
            'tooltip': await page.locator('#trackPaceTip').inner_text()}
        await page.screenshot(path=str(output / 'automatic-lap-pace.png'), full_page=True)

        await page.locator('#trackSelect').select_option('monza')
        await expect(page.locator('#trackAnnotations')).to_have_attribute('data-track-id', 'monza')
        await expect(page.locator('#trackReviewStatus')).to_contain_text('differs')
        await expect(page.locator('#trackPaceLegend')).to_be_hidden()
        await expect(page.locator('#trackPaceTip')).to_be_hidden()
        colors, counts = await canvas_colors(page), await pace_counts(page)
        assert colors == {'current': 0, 'reference': 0}, colors
        assert counts['current'] == counts['reference'] == 0, counts
        checks['mismatched_track_clears_overlay_and_hover'] = {'colors': colors, 'counts': counts}
        await page.screenshot(path=str(output / 'track-mismatch-no-pace.png'), full_page=True)
        assert not fixture.unexpected, fixture.unexpected
    finally:
        await context.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/track-pace-review')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks, passed, failure = [], {}, False, None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                await check_overlay(browser, f'http://127.0.0.1:{server.server_port}', errors, checks, args.output)
                await check_neutral_colors(browser, f'http://127.0.0.1:{server.server_port}', errors, checks, args.output)
                await check_automatic_alignment(browser, f'http://127.0.0.1:{server.server_port}', errors, checks, args.output)
                assert not errors, errors
                passed = True
            finally:
                await browser.close()
    except Exception as error:
        failure = f'{type(error).__name__}: {error}'
        raise
    finally:
        server.shutdown()
        report = {'passed': passed,
                  'scope': 'Synthetic in-memory lap fixtures, real frontend and Python comparison; local section pace and canvas pixels. No user library changes.',
                  'checks': checks, 'page_errors': errors, 'failure': failure}
        (args.output / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
