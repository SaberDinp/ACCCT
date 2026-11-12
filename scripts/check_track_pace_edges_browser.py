"""Regression checks for partial-lap gaps and manual multi-lap pace ambiguity.

Serves static frontend assets with in-memory API fixtures only. The fixtures
have known passage durations; they do not measure real-world map accuracy.
"""
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer

from playwright.async_api import async_playwright, expect

from check_track_car_progress_browser import (
    CURRENT, REFERENCE, ROOT, StaticHandler, new_page, path_point, preferences, seek,
)
from check_track_pace_browser import PaceFixtures, pace_counts


async def hover_progress(page, fixture, progress, reverse=False):
    x, y = path_point(fixture.track, fixture.finish + (-progress if reverse else progress))
    await page.locator('#trackMap').scroll_into_view_if_needed()
    box = await page.locator('#trackMap').bounding_box()
    size = min(box['width'], box['height']) - 40
    await page.mouse.move(box['x'] + (box['width'] - size) / 2 + x * size,
                          box['y'] + (box['height'] - size) / 2 + y * size)
    await expect(page.locator('#trackPaceTip')).to_be_visible()
    return {'winner': await page.locator('#trackPaceTip').get_attribute('data-winner'),
            'text': await page.locator('#trackPaceTip').text_content()}


async def check_clean_slice(browser, url, errors):
    result = {}
    for gap in (False, True):
        fixture = PaceFixtures()
        if gap:
            # Selected current range 10–60 s is clean. Its containing lap has
            # one unobserved speed at 100 s, outside the selected slice.
            fixture.sessions[CURRENT]['samples'][1000]['speed'] = None
        selected = preferences(axis='elapsed', current_range='custom', reference=REFERENCE, start=10, end=60)
        selected['reference'].update(range='custom', start=30, end=90)
        context, page, fixture = await new_page(browser, url, selected, errors, fixture)
        try:
            await expect(page.locator('#trackPaceLegend')).to_be_visible()
            counts = await pace_counts(page)
            assert counts['current'] > 15 and counts['reference'] > 15, (gap, counts)
            assert counts['unavailable'] >= 50, 'The unselected remainder must not gain fabricated pace colors.'
            faster = await hover_progress(page, fixture, .12)
            slower = await hover_progress(page, fixture, .37)
            assert faster['winner'] == 'current', faster
            assert slower['winner'] == 'reference', slower
            result['gap_elsewhere' if gap else 'no_gap'] = {'counts': counts, 'first_quarter': faster, 'second_quarter': slower}
            assert not fixture.unexpected, fixture.unexpected
        finally:
            await context.close()
    return result


async def check_manual_laps(browser, url, errors):
    fixture = PaceFixtures()
    for sid in (CURRENT, REFERENCE):
        prototype = fixture.sessions[sid]['samples'][0]
        # Your run is faster on the first passage and slower on the second.
        # Repeated circuit positions therefore cannot inherit lap-one winners.
        fixture.sessions[sid]['samples'] = [{**prototype, 'time': index / 10,
            'speed': 216 if sid == CURRENT and index < 1000 else 72 if sid == CURRENT else 144}
            for index in range(2601)]
    selected = preferences(axis='elapsed', current_range='custom', reference=REFERENCE, start=0, end=260)
    selected['reference'].update(range='custom', start=0, end=260)
    context, page, fixture = await new_page(browser, url, selected, errors, fixture)
    try:
        await page.locator('#trackProgressMode').select_option('manual')
        await page.locator('#trackSetOrigin').click()
        await page.locator('.track-timing[data-kind="start_finish"]').click()
        await page.locator('#trackRangesAligned').check()
        await seek(page, 170)
        counts = await pace_counts(page)
        assert counts['unavailable'] >= 90 and 10 <= counts['current'] < 35, counts
        # Keep the track hover separate from the car marker at25.6%.
        repeated = await hover_progress(page, fixture, .37)
        unique = await hover_progress(page, fixture, .9)
        assert repeated['winner'] == 'unavailable', repeated
        assert unique['winner'] == 'current', unique
        marker = await page.locator('#trackCarMarker').get_attribute('aria-label')
        assert '25.' in marker, marker
        await page.locator('#trackReverse').check()
        reversed_counts = await pace_counts(page)
        for key in ('current', 'reference', 'equal', 'unavailable'):
            assert counts[key] == reversed_counts[key], (counts, reversed_counts)
        reversed_repeated = await hover_progress(page, fixture, .37, reverse=True)
        reversed_unique = await hover_progress(page, fixture, .9, reverse=True)
        assert reversed_repeated['winner'] == 'unavailable', reversed_repeated
        assert reversed_unique['winner'] == 'current', reversed_unique
        assert not fixture.unexpected, fixture.unexpected
        return {'counts': counts, 'marker_on_second_lap': marker, 'repeated_section': repeated,
                'unique_section': unique, 'reverse_counts': reversed_counts,
                'reverse_repeated_section': reversed_repeated, 'reverse_unique_section': reversed_unique}
    finally:
        await context.close()


async def main():
    output = ROOT / 'artifacts/track-pace-edges'
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors, passed = {}, [], False
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                url = f'http://127.0.0.1:{server.server_port}'
                checks['clean_partial_survives_gap_elsewhere_in_lap'] = await check_clean_slice(browser, url, errors)
                checks['manual_repeated_passages_are_ambiguous_in_both_directions'] = await check_manual_laps(browser, url, errors)
                assert not errors, errors
                passed = True
            finally:
                await browser.close()
    finally:
        server.shutdown()
        report = {'passed': passed, 'checks': checks, 'page_errors': errors,
                  'scope': 'Synthetic passage fixtures and actual frontend; no saved recordings modified.'}
        (output / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
