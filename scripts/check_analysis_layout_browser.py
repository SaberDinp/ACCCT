"""Isolated Analysis video, map annotation, tooltip and responsive-layout checks."""
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_video_fixtures import VideoFixtures, make_clip, make_offset_clip, ROOT, SOCKET_FIXTURE, StaticHandler
from check_delete_swap_browser import CURRENT, REFERENCE, PROTECTED
from telemetry.catalog import catalog
from telemetry.tracks import get_track


class AnalysisFixtures(VideoFixtures):
    def __init__(self, clip):
        offset_clip = clip.with_name('analysis-offset.mp4')
        make_offset_clip(clip, offset_clip)
        super().__init__(clip, offset_clip)
        for sid, car_id, name in [(CURRENT, 'mclaren_720s_gt3_evo', 'McLaren 720S GT3 Evo'),
                                  (REFERENCE, 'bmw_m4_gt3', 'BMW M4 GT3')]:
            self.sessions[sid]['session']['context'] = {'car_id': car_id, 'car_name': name,
                                                       'track_id': 'silverstone', 'track_name': 'Silverstone'}
        self.track = get_track('silverstone')
        # Frontend contract fixtures, not claims about circuit locations.
        self.track.update(corners=[{'number': 1, 'name': 'Test corner', 'progress': .1},
            {'number': '8a', 'name': 'Group example', 'progress': .55},
            {'number': None, 'name': 'Named complex', 'progress': .8}],
                          straights=[{'name': 'Test straight', 'progress': .35}],
                          timing_lines=[{'kind': 'start_finish', 'label': 'Start / finish', 'progress': .015},
                                        {'kind': 'sector', 'label': 'Sector 1', 'sector': 1, 'progress': .4},
                                        {'kind': 'sector', 'label': 'Sector 2', 'sector': 2, 'progress': .7}])

    async def route(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/catalog':
            return await route.fulfill(json=catalog())
        if path == '/api/tracks/silverstone':
            return await route.fulfill(json=self.track)
        return await super().route(route)


async def main():
    output = ROOT / 'artifacts/analysis-layout'
    output.mkdir(parents=True, exist_ok=True)
    clip = ROOT / 'artifacts/desktop-video/source-1080p.mp4'
    make_clip(clip)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, results, inspector_requests = [], {}, []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1920, 'height': 1200}, device_scale_factor=1)
            fixture = AnalysisFixtures(clip)
            await page.route('**/api/**', fixture.route)
            await page.add_init_script(SOCKET_FIXTURE)
            preferences = {'axis': 'distance', 'alignment_mode': 'raw', 'align_start': False,
                'current': {'id': CURRENT, 'range': 'custom', 'start': 0, 'end': 7.8, 'segment': '1'},
                'reference': {'id': REFERENCE, 'range': 'custom', 'start': 0, 'end': 7.8, 'segment': '1'}}
            await page.add_init_script('localStorage.setItem("accc.comparison.v1", '+json.dumps(json.dumps(preferences))+');')
            # ACC geometry changed source order: legacy confirmations must not move cars.
            legacy_map = {'track':'silverstone','origin':.42,'reverse':True,'aligned':True,
                          'selectionKey':'legacy selection'}
            await page.add_init_script('localStorage.setItem('+json.dumps(f'accc.track-review.v1.{CURRENT}:{REFERENCE}')+', '+json.dumps(json.dumps(legacy_map))+');')
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: inspector_requests.append(request.url)
                    if '/frame.jpg?' in request.url and 'width=1920' in request.url else None)
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await page.get_by_role('button', name='Analysis', exact=True).click()
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=20000)
            await expect(page.locator('#trackSelect')).to_have_value('silverstone')
            await page.wait_for_function("document.querySelectorAll('.track-corner').length===3")
            await expect(page.locator('#trackOriginStatus')).to_have_text('Lap start / finish')
            await expect(page.locator('#trackRangesAligned')).not_to_be_checked()
            await expect(page.locator('#trackReverse')).not_to_be_checked()
            await expect(page.locator('#trackCarMarker')).to_be_hidden()
            await page.wait_for_function("['current','reference'].every(s=>document.querySelector('#track'+s+'Native').videoWidth===1920)")
            await page.locator('#trackReviewDistance').fill('100')
            await page.locator('#trackReviewDistance').dispatch_event('input')
            await page.wait_for_function("['current','reference'].every(s=>!document.querySelector('#track'+s+'Native').hidden)")
            results['native_videos'] = await page.locator('.track-video-button video').evaluate_all('vs => vs.map(v=>({width:v.videoWidth,height:v.videoHeight,time:v.currentTime,paused:v.paused}))')
            await expect(page.locator('#trackcurrentCar')).to_have_text('McLaren 720S GT3 Evo')
            await expect(page.locator('#trackreferenceCar')).to_have_text('BMW M4 GT3')
            await page.locator('#trackcurrentInspect').click()
            await expect(page.locator('#alignmentSetStart')).to_be_enabled()
            assert inspector_requests, 'Expanded frame must request 1920-pixel detail'
            await page.locator('#closeAlignment').click()
            await page.locator('#trackProgressMode').select_option('one_lap')
            await expect(page.locator('#trackCarMarker')).to_be_visible()
            await expect(page.locator('#trackReferenceCarMarker')).to_be_visible()
            await page.locator('#trackReviewPlay').click()
            await expect(page.locator('#trackReviewPlay')).to_have_attribute('aria-label','Pause comparison')
            results['native_playback'] = await page.locator('.track-video-button video').evaluate_all('''videos => Promise.all(videos.map(video => new Promise(resolve => {
              const times = []; let token;
              const present = (_, metadata) => {times.push(metadata.mediaTime); token = video.requestVideoFrameCallback(present);};
              token = video.requestVideoFrameCallback(present);
              setTimeout(() => {video.cancelVideoFrameCallback(token); resolve({frames:new Set(times).size, paused:video.paused, time:video.currentTime});}, 1200);
            })))''')
            assert all(item['frames'] > 20 and not item['paused'] for item in results['native_playback']), results['native_playback']
            assert float(await page.locator('#trackReviewDistance').input_value()) > 100
            await page.screenshot(path=str(output/'native-comparison-playing.png'),full_page=True)
            await page.locator('#trackReviewPlay').click()
            await expect(page.locator('#trackReviewPlay')).to_have_attribute('aria-label','Play comparison')
            await page.wait_for_function("['current','reference'].every(s=>document.querySelector('#track'+s+'Native').paused)")
            await page.wait_for_timeout(250)
            paused_times = await page.locator('.track-video-button video').evaluate_all('videos=>videos.map(v=>v.currentTime)')
            await page.wait_for_timeout(300)
            assert await page.locator('.track-video-button video').evaluate_all('videos=>videos.map(v=>v.currentTime)') == paused_times
            await page.locator('#trackReviewDistance').fill('100')
            await page.locator('#trackReviewDistance').dispatch_event('input')
            await page.locator('.track-corner[aria-label="Turn 1 · Test corner"]').hover()
            await expect(page.locator('.track-corner[aria-label="Turn 1 · Test corner"] .track-annotation-tip')).to_be_visible()
            await expect(page.locator('.track-corner[aria-label="Turn 1 · Test corner"] .track-annotation-tip')).to_have_text('Turn 1 · Test corner')
            await page.locator('.track-corner[aria-label="Turn 1 · Test corner"]').focus()
            await expect(page.locator('.track-corner[aria-label="Turn 1 · Test corner"] .track-annotation-tip')).to_be_visible()
            assert await page.locator('.track-straight').get_attribute('aria-label') == 'Test straight'
            assert await page.locator('.track-timing').count() == 3
            assert await page.locator('.track-complex').get_attribute('aria-label') == 'Named complex'
            assert await page.locator('.track-corner[aria-label="Turn 8a · Group example"]').count() == 1
            await page.locator('#trackProgressMode').select_option('manual')
            await page.locator('.track-timing[data-kind="start_finish"]').click()
            await page.locator('#trackRangesAligned').check()
            await expect(page.locator('#trackCarMarker')).to_be_visible()
            await page.locator('#trackCarMarker').hover()
            await expect(page.locator('#trackCarTip')).to_be_visible()
            await expect(page.locator('#trackCarTip')).to_contain_text('McLaren 720S GT3 Evo')
            await page.locator('#trackReferenceCarMarker').hover()
            await expect(page.locator('#trackReferenceCarTip')).to_be_visible()
            await expect(page.locator('#trackReferenceCarTip')).to_contain_text('BMW M4 GT3')
            for width,height in [(1920,1200),(1440,1000),(1280,900),(768,1024),(390,844)]:
                await page.set_viewport_size({'width':width,'height':height})
                await page.locator('#referenceWorkspace').scroll_into_view_if_needed()
                await page.wait_for_timeout(120)
                assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'), width
                if width >= 1280:
                    videos = await page.locator('.track-video-button').evaluate_all('es=>es.map(e=>{let r=e.getBoundingClientRect();return {width:r.width,height:r.height,top:r.top,left:r.left,right:r.right};})')
                    assert all(v['width'] >= 500 for v in videos), videos
                    assert abs(videos[0]['top']-videos[1]['top'])<2, videos
                await page.screenshot(path=str(output/f'analysis-{width}.png'),full_page=True)
            await page.set_viewport_size({'width':1440,'height':1000})
            await page.locator('#trackExpand').click()
            await page.wait_for_function('document.fullscreenElement?.classList.contains("track-map-stage")')
            await expect(page.locator('.track-complex')).to_be_visible()
            await page.locator('#trackCollapse').click()
            await page.wait_for_function('!document.fullscreenElement')
            # A switch must discard native media from the previous source.
            await page.locator('#referenceSession').select_option('')
            await page.wait_for_function("document.querySelector('#trackreferenceNative').getAttribute('src')===null")
            await expect(page.locator('#trackreferenceVideo')).to_be_hidden()
            await page.locator('#currentSession').select_option(PROTECTED)
            await expect(page.locator('#comparisonStatus')).to_have_text('',timeout=20000)
            await expect(page.locator('#trackcurrentNative')).to_be_visible(timeout=15000)
            await page.wait_for_function('document.querySelector("#trackcurrentNative").currentTime>=4')
            results['nonzero_source_pts'] = await page.locator('#trackcurrentNative').evaluate('v=>({time:v.currentTime,width:v.videoWidth})')
            assert results['nonzero_source_pts']['time'] >= 4
            fixture.decode_failures.add(CURRENT)
            await page.locator('#currentSession').select_option(CURRENT)
            await expect(page.locator('#comparisonStatus')).to_have_text('',timeout=20000)
            await page.wait_for_function('document.querySelector("#trackcurrentNative").error!==null')
            await expect(page.locator('#trackcurrentNative')).to_be_hidden()
            await expect(page.locator('#trackcurrentImage')).to_be_visible()

            assert not errors, errors
            assert not fixture.unexpected, fixture.unexpected
            results.update({'page_errors':errors,'checks':['1080p native paired videos','cursor synchronization','car labels',
                'corner hover and keyboard focus','straight names','finish and sector overlays','separate manual origin',
                'car-marker hover','five responsive sizes','expanded map','1920px frame inspection','continuous native comparison playback','cleared-source native cleanup','legacy geometry origin ignored','nonzero source PTS','native decode failure frame fallback'],
                'annotation_scope':'Synthetic frontend contract locations; real circuit annotations tested separately.'})
            await browser.close()
    finally:
        server.shutdown()
    (output/'report.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
