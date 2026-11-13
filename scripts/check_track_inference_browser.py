"""Circuit-guide confidence, preference and async-race checks with local fixtures.

Runs the real track guide with a small comparison host. API answers are explicit
contract fixtures; backend telemetry matching has separate Python tests.
"""
import asyncio
import functools
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from telemetry.tracks import get_track, list_tracks

HARNESS = '''<!doctype html><html><head><link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/track-review.css"></head><body><main><div id="referenceWorkspace"><div class="compare-toolbar"></div><div class="comparison-legend"></div><div class="comparison-empty-status"></div><div class="comparison-body"></div><div class="compare-summary-line"></div></div></main></body></html>'''


def match(track):
    return {'status': 'matched', 'track_id': track, 'track_name': get_track(track)['name'],
            'source': 'telemetry', 'confidence': 'high', 'auto_select': True,
            'reason': 'Synthetic confident fixture', 'evidence': {}, 'candidates': []}


class Fixture:
    def __init__(self, replies=None, held=()):
        self.replies = replies or {}
        self.gates = {key: asyncio.Event() for key in held}
        self.requests, self.finished = [], []

    async def route(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/tracks':
            return await route.fulfill(json=list_tracks())
        if path.startswith('/api/tracks/'):
            return await route.fulfill(json=get_track(path.rsplit('/', 1)[1]))
        if path.endswith('/video'):
            return await route.fulfill(json={'state': 'unavailable', 'url': None})
        assert path == '/api/track-inference', path
        body = route.request.post_data_json
        assert set(body) == {'session_id', 'range', 'range_is_lap'}, body
        assert set(body['range']) <= {'start_time', 'end_time', 'segment_id'}, body
        assert body['range']['start_time'] == 0 and body['range']['end_time'] == 120, body
        self.requests.append(body)
        key = body['session_id']
        if key in self.gates:
            await self.gates[key].wait()
        result = self.replies.get(key, {'status': 'ambiguous', 'track_id': None,
            'track_name': None, 'source': 'none', 'confidence': 'none', 'auto_select': False,
            'reason': 'Synthetic ambiguous fixture', 'evidence': {},
            'candidates': [match('silverstone'), match('spa')]})
        await route.fulfill(json=result)
        self.finished.append(key)


async def wait_until(predicate):
    for _ in range(250):
        if predicate():
            return
        await asyncio.sleep(.02)
    raise AssertionError('Fixture request did not settle')


async def open_guide(browser, url, fixture, errors, saved=None):
    context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
    await context.route('**/api/**', fixture.route)
    await context.route('**/__track_harness', lambda route: route.fulfill(content_type='text/html', body=HARNESS))
    if saved:
        await context.add_init_script('localStorage.setItem("accc.track-review.v2.current:", '+json.dumps(json.dumps(saved))+');')
    page = await context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    await page.goto(url + '/__track_harness')
    await page.add_script_tag(path=str(ROOT / 'web/track-pace.js'))
    await page.add_script_tag(path=str(ROOT / 'web/track-review.js'))
    await page.evaluate('''() => {
      window.guide = {lap:true, label:null};
      const trace = {selection:{start_time:0,end_time:120,start_index:0,end_index:1200,requested_start_time:0},raw_distance:6000,distance:6000,distance_complete:true};
      const points=[0,60,120].map(time=>({position:time*50,current:{time,distance:time*50,raw_distance:time*50,speed:180,gear:3,brake:0,throttle:100}}));
      guide.compare = (current='current', reference=null) => {
        const result={axis:'distance',extent:[0,6000],current:structuredClone(trace),points:structuredClone(points)};
        if(reference){result.reference=structuredClone(trace);for(const point of result.points)point.reference={...point.current};}
        guide.result=result;
        ACCCTrackReview.setComparison(result,{current,reference});
        ACCCTrackReview.setCursor(result.points[1]);
      };
      ACCCTrackReview.init({api:async(path,options)=>{const response=await fetch(path,{...options,headers:{'Content-Type':'application/json'}});if(!response.ok)throw Error(response.status);return response.json();},icons:()=>{},trackId:()=>guide.label,
        rangeInfo:()=>({lap:guide.lap,segment:1,lapRanges:[]}),sourceSamples:()=>[],playback:{pause:()=>{},toggle:()=>{}},inspect:()=>{},seek:position=>ACCCTrackReview.setCursor(guide.result.points.reduce((best,p)=>Math.abs(p.position-position)<Math.abs(best.position-position)?p:best))});
    }''')
    await page.wait_for_function("document.querySelector('#trackSelect').options.length>2")
    return context, page


async def selected(page, track):
    await expect(page.locator('#trackSelect')).to_have_value(track)
    await expect(page.locator('#trackAnnotations')).to_have_attribute('data-track-id', track)


async def main():
    output = ROOT / 'artifacts/track-inference-browser'
    output.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT / 'web'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors = {}, []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            url = f'http://127.0.0.1:{server.server_port}'
            fixture = Fixture({'current': match('silverstone')})
            context, page = await open_guide(browser, url, fixture, errors)
            await page.evaluate('guide.compare()')
            await selected(page, 'silverstone')
            await expect(page.locator('#trackDetectionStatus')).to_contain_text('matched from lap telemetry')
            assert fixture.requests[0]['range_is_lap'] is True
            await expect(page.locator('#trackProgressMode, #trackManualControls, #trackcurrentCar, #trackreferenceCar')).to_have_count(0)
            await expect(page.locator('#trackCarMarker')).to_be_visible()
            await expect(page.locator('#trackCarMarker')).to_have_attribute('data-progress', '0.5')
            checks['confident_match_sanitized_range_and_automatic_progress'] = True
            await page.locator('#trackSelect').select_option('')
            await page.evaluate('guide.compare()')
            await expect(page.locator('#trackSelect')).to_have_value('')
            assert len(fixture.requests) == 1
            checks['explicit_empty_selection_prevents_auto_reselection'] = True
            await context.close()

            fixture = Fixture({'current': match('silverstone')}, held=['current'])
            context, page = await open_guide(browser, url, fixture, errors)
            await page.evaluate('guide.compare()')
            await wait_until(lambda: len(fixture.requests) == 1)
            await page.locator('#trackSelect').select_option('spa')
            await selected(page, 'spa')
            fixture.gates['current'].set()
            await wait_until(lambda: fixture.finished)
            await page.wait_for_timeout(100)
            await selected(page, 'spa')
            saved = json.loads(await page.evaluate('localStorage.getItem("accc.track-review.v2.current:")'))
            assert saved == {'track':'spa', 'selectionSource':'manual'}, saved
            await context.close()
            restored = Fixture({'current':match('silverstone')})
            context, page = await open_guide(browser, url, restored, errors, saved=saved)
            await page.evaluate('guide.compare()')
            await selected(page, 'spa')
            assert not restored.requests
            checks['manual_choice_beats_pending_inference_and_survives_restore'] = True
            await context.close()

            fixture = Fixture({'old': match('silverstone'), 'new': match('monza')}, held=['old'])
            context, page = await open_guide(browser, url, fixture, errors)
            await page.evaluate("guide.compare('old')")
            await wait_until(lambda: fixture.requests)
            await page.evaluate("guide.compare('new')")
            await selected(page, 'monza')
            fixture.gates['old'].set()
            await wait_until(lambda: 'old' in fixture.finished)
            await page.wait_for_timeout(100)
            await selected(page, 'monza')
            checks['stale_recording_inference_cannot_replace_new_recording'] = True
            await context.close()

            fixture = Fixture()
            context, page = await open_guide(browser, url, fixture, errors)
            await page.evaluate('guide.lap=false;guide.compare()')
            await wait_until(lambda: fixture.finished)
            assert fixture.requests[0]['range_is_lap'] is False
            await expect(page.locator('#trackSelect')).to_have_value('')
            checks['arbitrary_range_is_not_claimed_as_complete_lap'] = True
            await context.close()

            for name, replies, reference in [('ambiguous', {}, None),
                    ('conflicting_pair', {'current':match('silverstone'), 'reference':match('spa')}, 'reference'),
                    ('unconfident', {'current':{**match('silverstone'), 'confidence':'none'}}, None)]:
                fixture = Fixture(replies)
                context, page = await open_guide(browser, url, fixture, errors)
                await page.evaluate('(reference)=>guide.compare("current",reference)', reference)
                await wait_until(lambda: len(fixture.finished) == (2 if reference else 1))
                await expect(page.locator('#trackDetectionStatus')).not_to_contain_text('Checking')
                await expect(page.locator('#trackSelect')).to_have_value('')
                checks[name+'_does_not_auto_select'] = True
                await context.close()

            saved={'track':'silverstone','progressMode':'manual','origin':.42,'reverse':True,'aligned':True}
            fixture = Fixture()
            context, page = await open_guide(browser, url, fixture, errors, saved=saved)
            await page.evaluate('guide.compare()')
            await selected(page, 'silverstone')
            await expect(page.locator('#trackCarMarker')).to_have_attribute('data-progress', '0.5')
            await page.evaluate('guide.lap=false;guide.compare()')
            await expect(page.locator('#trackCarMarker')).to_be_hidden()
            assert not fixture.requests
            checks['legacy_manual_geometry_ignored_and_unknown_partial_not_forced_to_lap'] = True
            await page.screenshot(path=str(output/'automatic-only-guide.png'), full_page=True)
            await context.close()
            assert not errors, errors
            await browser.close()
    finally:
        server.shutdown()
        (output/'report.json').write_text(json.dumps({'checks':checks,'page_errors':errors,
            'scope':'Real track-guide frontend with synthetic API confidence and race fixtures.'},indent=2)+'\n')
    print(json.dumps({'checks':checks,'page_errors':errors},indent=2))


if __name__ == '__main__':
    asyncio.run(main())
