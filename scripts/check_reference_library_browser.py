"""Isolated reference-library and browser-video regression; no videos downloaded."""
import argparse
import asyncio
import copy
import functools
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect
from browser_fixtures import SOCKET_FIXTURE, StaticHandler
from check_delete_swap_browser import ROOT, CURRENT, REFERENCE, PROTECTED, PREFERENCES, Fixtures, Gate, loaded

OLD, NEW = 'older-cached-reference', 'new-imported-reference'


class LibraryFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.sessions[OLD] = copy.deepcopy(self.sessions[REFERENCE])
        self.sessions[OLD]['session'].update(id=OLD, name='Older analyzed lap')
        self.images[OLD] = self.images[REFERENCE]
        self.reference_reads, self.import_posts, self.new_jobs = 0, [], 0
        self.fail_library = self.fail_import = self.completed = False
        self.library_gate = None
        self.entries = [
            {'id': 'fresh-lap', 'track_id': 'monza', 'car_id': 'car_a', 'title': 'Fixture lap A',
             'creator': {'name': 'Fixture creator'}, 'url': 'https://video.example.test/watch/a',
             'lap_time_text': '1:47.123', 'game_version': '1.10', 'hud_verification': 'not_reviewed'},
            {'id': 'saved-lap', 'track_id': 'monza', 'car_id': 'car_a', 'title': 'Fixture saved lap',
             'creator': {'name': 'Second fixture creator'}, 'url': 'https://video.example.test/watch/b',
             'lap_time_text': None, 'game_version': None},
            {'id': 'other-circuit', 'track_id': 'silverstone', 'car_id': 'car_a', 'title': 'Fixture Silverstone lap',
             'creator': {'name': 'Fixture creator'}, 'url': 'https://video.example.test/watch/c'}]

    async def route(self, route):
        request = route.request
        parsed = urlparse(request.url)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == '/api/reference-library':
            self.reference_reads += 1
            if self.fail_library:
                await route.fulfill(status=503, json={'detail': 'Fixture library temporarily unavailable.'})
                return
            if self.library_gate and query.get('track_id') == ['monza'] and query.get('car_id') == ['car_a']:
                gate, self.library_gate = self.library_gate, None
                await gate.wait()
            entries = copy.deepcopy(self.entries)
            for entry in entries:
                entry['cache'] = {'state': 'not_cached', 'session_id': None, 'job_id': None}
                if entry['id'] == 'saved-lap':
                    entry['cache'] = {'state': 'ready', 'session_id': OLD, 'job_id': None}
                elif entry['id'] == 'fresh-lap' and self.new_jobs:
                    entry['cache'] = {'state': 'ready' if self.completed else 'importing',
                                      'session_id': NEW if self.completed else None, 'job_id': 'library-job'}
            entries = [entry for entry in entries if all(not query.get(key) or entry[key] == query[key][0] for key in ('car_id', 'track_id'))]
            await route.fulfill(json={'tracks': [{'id':'monza','name':'Monza'}, {'id':'silverstone','name':'Silverstone'}],
                'cars': [{'id':'car_a','name':'Fixture GT3 car'}, {'id':'car_b','name':'Fixture GT4 car'}],
                'references': entries, 'count':len(entries), 'updated_at':'2026-11-13'})
            return
        if path.startswith('/api/reference-library/') and path.endswith('/import'):
            entry = path.split('/')[3]
            self.import_posts.append(entry)
            assert request.method == 'POST'
            if self.fail_import:
                await route.fulfill(status=503, json={'detail':'The fixture video is unavailable. Try another lap.'})
            elif entry == 'saved-lap':
                await route.fulfill(json={'reference_id':entry,'state':'ready','session_id':OLD,'job':None,'reused':True})
            else:
                reused = bool(self.new_jobs)
                self.new_jobs = 1
                await route.fulfill(json={'reference_id':entry,'state':'importing','session_id':None,
                    'job':{'id':'library-job','role':'reference','state':'queued'},'reused':reused})
            return
        if path == '/api/imports':
            jobs = []
            if self.new_jobs:
                if self.completed and NEW not in self.sessions:
                    self.sessions[NEW] = copy.deepcopy(self.sessions[REFERENCE])
                    self.sessions[NEW]['session'].update(id=NEW, name='New reference lap')
                    self.images[NEW] = self.images[REFERENCE]
                jobs = [{'id':'library-job','name':'Fixture lap A','role':'reference',
                    'state':'completed' if self.completed else 'queued','session_id':NEW if self.completed else None}]
            await route.fulfill(json=jobs)
            return
        if path == '/api/sessions':
            await route.fulfill(json=[data['session'] for sid,data in self.sessions.items() if sid != OLD or OLD in query.get('include',[])])
            return
        if path.endswith('/watch') and '/api/sessions/' in path:
            sid = path.split('/')[3]
            if '/video/watch' in path:
                await route.fulfill(content_type='text/html', body='<!doctype html><title>Recording player</title><video controls preload="none"></video>')
            elif sid == PROTECTED:
                await route.fulfill(json={'available':True,'kind':'external','url':'javascript:alert(1)'})
            else:
                await route.fulfill(json={'available':True,'kind':'external' if sid == REFERENCE else 'local',
                    'url':'https://video.example.test/watch/reference' if sid == REFERENCE else f'/api/sessions/{sid}/video/watch'})
            return
        await super().route(route)


async def choose(page, track='monza', car='car_a'):
    await expect(page.locator('#referenceLibraryTrack')).to_be_enabled()
    await page.locator('#referenceLibraryTrack').select_option(track)
    await page.locator('#referenceLibraryCar').select_option(car)
    await expect(page.locator('#referenceLibraryStatus')).not_to_have_text('Loading reference laps…')


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1',0), functools.partial(StaticHandler,directory=str(ROOT/'web')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    errors,checks = [],{}
    fixture = LibraryFixtures()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport={'width':1440,'height':1050})
            await context.route('**/api/**',fixture.route)
            await context.route('https://video.example.test/**',lambda route:route.fulfill(content_type='text/html',body='<title>Fixture original video</title>'))
            await context.add_init_script(SOCKET_FIXTURE)
            await context.add_init_script("""if(!sessionStorage.getItem('reference-library-fixture')) {
              localStorage.clear(); localStorage.setItem('accc.comparison.v1', %s);
              sessionStorage.setItem('reference-library-fixture','1');
            }""" % json.dumps(json.dumps(PREFERENCES)))
            page = await context.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await loaded(page)
            assert fixture.reference_reads == 0 and not fixture.import_posts
            for side in ('current','reference'):
                await expect(page.locator(f'#{side}OpenVideo')).to_have_attribute('aria-disabled','false')
                async with context.expect_page() as popup:
                    await page.locator(f'#{side}OpenVideo').click()
                video_page = await popup.value
                await video_page.wait_for_load_state()
                if side == 'current':
                    assert '/video/watch' in video_page.url
                    assert await video_page.locator('video').evaluate('v => v.controls && !v.muted')
                else:
                    assert video_page.url == 'https://video.example.test/watch/reference'
                await video_page.close()
            await page.locator('#currentSession').select_option(PROTECTED)
            await expect(page.locator('#currentRecordingStatus')).to_have_text('READY')
            await expect(page.locator('#currentOpenVideo')).to_have_attribute('aria-disabled','true')
            assert await page.locator('#currentOpenVideo').get_attribute('href') is None
            await page.locator('#currentSession').select_option(CURRENT)
            checks['original_and_local_videos_open_with_safe_protocols'] = True

            await page.locator('#findReferenceLap').click()
            await choose(page)
            await expect(page.locator('.reference-library-card')).to_have_count(2)
            await expect(page.locator('#referenceLibraryResults')).to_contain_text('Fixture creator · Lap 1:47.123 · ACC 1.10')
            await expect(page.locator('[data-reference-id="saved-lap"]')).to_have_text('Use saved lap')
            assert not fixture.import_posts and fixture.new_jobs == 0
            checks['multiple_attributed_laps_browse_without_downloading'] = True
            await choose(page,car='car_b')
            await expect(page.locator('#referenceLibraryStatus')).to_contain_text('No reference laps')
            await expect(page.locator('.reference-library-card')).to_have_count(0)
            await page.locator('#referenceLibraryPaste').click()
            await expect(page.locator('#referenceImportDialog')).to_be_visible()
            await expect(page.locator('#referenceUrl')).to_be_visible()
            await expect(page.locator('#recordingImportRole')).to_have_value('reference')
            await page.locator('#closeReferenceImport').click()
            checks['missing_combinations_have_video_link_fallback'] = True

            fixture.fail_library = True
            await page.locator('#findReferenceLap').click()
            await expect(page.locator('#referenceLibraryError')).to_contain_text('temporarily unavailable')
            fixture.fail_library = False
            await page.locator('#referenceLibraryRetry').click()
            await choose(page)
            await expect(page.locator('.reference-library-card')).to_have_count(2)
            checks['library_loading_error_can_retry'] = True

            fixture.library_gate = gate = Gate()
            await page.locator('#referenceLibraryCar').select_option('car_a')
            await asyncio.wait_for(gate.arrived.wait(),10)
            await page.locator('#referenceLibraryTrack').select_option('silverstone')
            await expect(page.locator('#referenceLibraryResults')).to_contain_text('Fixture Silverstone lap')
            gate.release.set()
            await page.wait_for_timeout(100)
            await expect(page.locator('.reference-library-card')).to_have_count(1)
            await expect(page.locator('#referenceLibraryResults')).to_contain_text('Fixture Silverstone lap')
            checks['stale_filter_response_cannot_replace_latest_selection'] = True

            await choose(page)
            await page.locator('[data-reference-id="saved-lap"]').click()
            await expect(page.locator('#referenceLibraryDialog')).to_be_hidden()
            await expect(page.locator('#referenceSession')).to_have_value(OLD)
            await expect(page.locator('#currentSession')).to_have_value(CURRENT)
            assert fixture.new_jobs == 0
            await page.locator('#refreshComparisons').click()
            await expect(page.locator('#referenceSession')).to_have_value(OLD)
            await page.reload()
            await loaded(page)
            await expect(page.locator('#referenceSession')).to_have_value(OLD)
            checks['older_cached_lap_reused_and_restored_without_new_job'] = True

            await page.locator('#findReferenceLap').click()
            await choose(page)
            fixture.fail_import = True
            await page.locator('[data-reference-id="fresh-lap"]').click()
            await expect(page.locator('#referenceLibraryError')).to_contain_text('video is unavailable')
            await expect(page.locator('#referenceSession')).to_have_value(OLD)
            fixture.fail_import = False
            await page.locator('[data-reference-id="fresh-lap"]').click()
            await expect(page.locator('#referenceLibraryDialog')).to_be_hidden()
            await expect(page.locator('#activeImport')).to_be_visible()
            await page.locator('#findReferenceLap').click()
            await expect(page.locator('[data-reference-id="fresh-lap"]')).to_have_text('Use when ready')
            await page.locator('[data-reference-id="fresh-lap"]').click()
            await expect(page.locator('#referenceLibraryDialog')).to_be_hidden()
            assert fixture.new_jobs == 1
            fixture.completed = True
            await expect(page.locator('#referenceSession')).to_have_value(NEW,timeout=15000)
            await expect(page.locator('#currentSession')).to_have_value(CURRENT)
            checks['explicit_import_reuses_inflight_job_and_loads_when_complete'] = True

            await page.locator('#findReferenceLap').click()
            await expect(page.locator('.reference-library-card')).to_have_count(2)
            await page.screenshot(path=str(args.output/'reference-library-desktop.png'))
            await page.set_viewport_size({'width':390,'height':844})
            box = await page.locator('#referenceLibraryDialog').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= 391,box
            assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
            await page.screenshot(path=str(args.output/'reference-library-mobile.png'))
            checks['picker_fits_desktop_and_mobile'] = True
            assert not errors,errors
            assert not fixture.unexpected,fixture.unexpected
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    report = {'checks':checks,'page_errors':errors,'import_requests':fixture.import_posts,
              'new_import_jobs':fixture.new_jobs,'real_network_downloads':0,'real_library_mutated':False}
    (args.output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/reference-library-review')
    asyncio.run(run(parser.parse_args()))
