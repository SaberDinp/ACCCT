"""Isolated delta-curve regression using real comparison/context code and pixels.

Known synthetic laps cover missing observations, unequal lengths and cars, and
unconfirmed metadata. Explicit sparse/singleton payloads test rendering gaps.
No user sessions or running application state are touched.
"""
import asyncio
import copy
import functools
import json
import math
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from check_track_car_progress_browser import ProgressFixtures, preferences, seek
from check_delete_swap_browser import CURRENT, REFERENCE, ROOT, SOCKET_FIXTURE, StaticHandler
from telemetry.comparison import compare_session_ranges
from telemetry.session_context import enrich_comparison, session_context


class DeltaFixtures(ProgressFixtures):
    def __init__(self, gaps=False, unknown=False, different_track=False, rendering=None):
        super().__init__()
        self.rendering = rendering
        for sid, car in ((CURRENT,'mclaren_720s_gt3_evo'),(REFERENCE,'bmw_m4_gt3')):
            session = self.sessions[sid]['session']
            session['metadata']['telemetry_context'] = {'car_id':car,
                'track_id':None if unknown else 'spa' if different_track and sid == REFERENCE else 'silverstone'}
            session['context'] = session_context(session)
        if gaps:
            for sid, start, end in ((CURRENT,50,54),(REFERENCE,105,109)):
                for sample in self.sessions[sid]['samples']:
                    if start < sample['time'] < end:
                        sample.update(active=False,speed=None,brake=None,throttle=None)

    async def route(self, route):
        if urlparse(route.request.url).path != '/api/compare':
            return await super().route(route)
        body = route.request.post_data_json
        current = self.sessions[body['current_session_id']]
        reference = self.sessions[body['reference_session_id']]
        try:
            result = compare_session_ranges(reference['samples'],current['samples'],
                reference_range=body['reference_range'],current_range=body['current_range'],
                axis=body['axis'],align_start=body['align_start'],align_end=body['align_end'],
                alignment_mode=body['alignment_mode'],landmarks=body['landmarks'])
            enrich_comparison(result,current['session'],reference['session'],body.get('confirm_same_track',False))
        except ValueError as error:
            return await route.fulfill(status=400,json={'detail':str(error)})
        result['sessions'] = {'current':current['session'],'reference':reference['session']}
        if self.rendering:
            # Frontend contract fixtures preserve real channel samples but make
            # segment boundaries wide enough for unambiguous canvas assertions.
            source = result['points']
            points = [copy.deepcopy(source[round(i*(len(source)-1)/10)]) for i in range(11)]
            for index,point in enumerate(points):
                point['delta_segment_id'] = 'first' if index <= 3 else 'last'
                point['delta_time_s'] = float(index-5) if index <= 3 or index >= 7 else None
                if self.rendering == 'segments':
                    point['delta_time_s'] = float(index-5)
            if self.rendering == 'singleton':
                for index,point in enumerate(points):
                    point['delta_time_s'] = 2.0 if index == 5 else None
            result['points'] = points
            result['delta_available'] = True
        self.results.append(result)
        return await route.fulfill(json=result)


async def canvas_delta(page, start=0, end=1):
    return await page.locator('#synchronizedChart').evaluate('''(canvas, bounds) => {
      const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height,scale=w/canvas.getBoundingClientRect().width;
      const lane=(h/scale-34)/6,top=(10+5*lane+21)*scale,bottom=(10+6*lane-10)*scale;
      const left=47*scale,plot=w-60*scale,from=Math.ceil(left+bounds[0]*plot),to=Math.floor(left+bounds[1]*plot);
      const data=ctx.getImageData(0,0,w,h).data;let pixels=0;
      for(let y=Math.ceil(top);y<bottom;y++)for(let x=from;x<=to;x++){
        const i=(y*w+x)*4;
        if(data[i+3]>100&&Math.hypot(data[i]-213,data[i+1]-233,data[i+2]-145)<24)pixels++;
      }
      return pixels;
    }''',[start,end])


async def open_fixture(browser, url, fixture, errors, axis='distance', registration=None):
    context=await browser.new_context(viewport={'width':1600,'height':1100},device_scale_factor=1)
    await context.route('**/api/**',fixture.route)
    await context.add_init_script(SOCKET_FIXTURE)
    selected=preferences(axis=axis,reference=REFERENCE)
    if registration:
        selected.update(alignment_mode=registration,align_start=True,align_end=True)
    await context.add_init_script('localStorage.setItem("accc.comparison.v1",'+json.dumps(json.dumps(selected))+');')
    page=await context.new_page()
    page.on('pageerror',lambda error:errors.append(str(error)))
    await page.goto(url)
    await page.get_by_role('button',name='Analysis',exact=True).click()
    await expect(page.locator('#runComparison')).to_have_attribute('aria-busy','false',timeout=20000)
    return context,page


async def settled(page):
    await expect(page.locator('#comparisonStatus')).to_have_text('',timeout=20000)
    await page.evaluate('() => new Promise(resolve => requestAnimationFrame(()=>requestAnimationFrame(resolve)))')


async def main():
    output=ROOT/'artifacts/delta-review';output.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(StaticHandler,directory=str(ROOT/'web')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    errors=[];checks={}
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            url=f'http://127.0.0.1:{server.server_port}'
            fixture=DeltaFixtures()
            context,page=await open_fixture(browser,url,fixture,errors)
            await settled(page)
            result=fixture.results[-1]
            assert result['delta_available'] and result['delta_mode']=='approximate'
            assert result['context']['different_cars'] and not result['align_start']
            assert math.isclose(result['current']['distance'],6000,abs_tol=.01)
            assert math.isclose(result['reference']['distance'],6250,abs_tol=.01)
            assert await canvas_delta(page,.02,.95)>100
            await expect(page.locator('.delta-sign-note')).to_contain_text('Estimated delta at matched distance')
            await expect(page.locator('#comparisonDeltaSummary')).to_contain_text('last comparable point')
            await seek(page,3000)
            row=page.locator('#comparisonValues tr').filter(has=page.get_by_text('Delta / s',exact=True))
            await expect(row).to_contain_text('-12.000')
            checks['unconfirmed_different_car_unequal_length_distance_delta']={'pixels':await canvas_delta(page),'cursor_delta':await row.inner_text()}
            await page.locator('#axisElapsed').click();await settled(page)
            await seek(page,60)
            await expect(row).to_contain_text('-15.000')
            await expect(page.locator('.delta-sign-note')).to_contain_text('matched lap progress')
            assert await canvas_delta(page,.02,.7)>100
            checks['elapsed_axis_compares_same_progress_not_same_timestamp']=await row.inner_text()
            await page.locator('#synchronizedChart').screenshot(path=str(output/'same-progress-time-axis.png'))
            await context.close()

            fixture=DeltaFixtures(gaps=True)
            context,page=await open_fixture(browser,url,fixture,errors,registration='lap_endpoints')
            await settled(page)
            result=fixture.results[-1]
            assert result['alignment']['mode']=='raw' and not result['alignment']['applied']
            assert result['alignment'].get('requested_mode','raw')=='raw'
            assert not result['align_start'] and not result['align_end']
            assert result['delta_available'] and result['delta_mode']=='approximate'
            assert any(point['delta_time_s'] is None for point in result['points'])
            assert result['current']['has_gaps'] and result['reference']['has_gaps']
            assert await canvas_delta(page)>100
            await expect(page.locator('#comparisonAlignmentMode, #confirmAlignment, #confirmFinishAlignment, #registrationStatus, #landmarkRows')).to_have_count(0)
            await expect(page.locator('#compareAxisNote')).not_to_contain_text('Registered')
            saved=json.loads(await page.evaluate('localStorage.getItem("accc.comparison.v1")'))
            assert saved['alignment_mode']=='raw' and saved['landmarks']==[]
            assert not saved['align_start'] and not saved['align_end']
            checks['legacy_registration_preferences_migrate_to_raw_with_gaps_and_delta']={'pixels':await canvas_delta(page),'delta_mode':result['delta_mode']}
            await page.screenshot(path=str(output/'legacy-registration-raw-delta.png'),full_page=True)
            await context.close()

            fixture=DeltaFixtures(gaps=True,unknown=True)
            context,page=await open_fixture(browser,url,fixture,errors,axis='elapsed')
            await settled(page)
            result=fixture.results[-1]
            finite=[point for point in result['points'] if point['delta_time_s'] is not None]
            missing=[point for point in result['points'] if point['delta_time_s'] is None]
            assert result['delta_available'] and finite and missing and not result['context']['same_track']
            assert len({point['delta_segment_id'] for point in finite})>=2
            assert await canvas_delta(page)>100
            assert await canvas_delta(page,.02,.2)>10 and await canvas_delta(page,.55,.75)>10
            independently_matched=next(point for point in finite if point['reference'] is None)
            await seek(page,independently_matched['position'])
            row=page.locator('#comparisonValues tr').filter(has=page.get_by_text('Delta / s',exact=True))
            expected=f"{independently_matched['delta_time_s']:+.3f}" if independently_matched['delta_time_s']>0 else f"{independently_matched['delta_time_s']:.3f}"
            await expect(row).to_contain_text(expected)
            checks['matching_progress_delta_survives_missing_simultaneous_reference']=expected
            await expect(page.locator('#comparisonDeltaSummary')).not_to_contain_text('Awaiting')
            checks['gaps_and_unknown_track_preserve_usable_sections']={'finite':len(finite),'missing':len(missing),'pixels':await canvas_delta(page)}
            await page.locator('#synchronizedChart').screenshot(path=str(output/'gapped-unconfirmed-delta.png'))
            await context.close()

            fixture=DeltaFixtures(rendering='sparse')
            context,page=await open_fixture(browser,url,fixture,errors)
            await settled(page)
            assert await canvas_delta(page,.03,.27)>20 and await canvas_delta(page,.73,.95)>20
            assert await canvas_delta(page,.42,.58)==0
            await seek(page,fixture.results[-1]['extent'][1]*.5)
            row=page.locator('#comparisonValues tr').filter(has=page.get_by_text('Delta / s',exact=True))
            await expect(row).to_contain_text('--')
            checks['sparse_missing_delta_is_not_bridged_in_plot_or_cursor']=True
            await page.locator('#synchronizedChart').screenshot(path=str(output/'sparse-delta-break.png'))
            await context.close()

            fixture=DeltaFixtures(rendering='segments')
            context,page=await open_fixture(browser,url,fixture,errors)
            await settled(page)
            result=fixture.results[-1]
            before,after=result['points'][3:5]
            position=(before['position']+after['position'])/2
            fraction=position/result['extent'][1]
            assert await canvas_delta(page,fraction-.008,fraction+.008)==0
            await seek(page,position)
            row=page.locator('#comparisonValues tr').filter(has=page.get_by_text('Delta / s',exact=True))
            await expect(row).to_contain_text('--')
            checks['finite_adjacent_segments_are_not_connected_or_interpolated']=True
            await context.close()

            fixture=DeltaFixtures(rendering='singleton')
            context,page=await open_fixture(browser,url,fixture,errors)
            await settled(page)
            pixels=await canvas_delta(page,.4,.6)
            assert pixels>=3,pixels
            checks['isolated_finite_delta_is_visible_dot']=pixels
            await context.close()

            fixture=DeltaFixtures(different_track=True)
            context,page=await open_fixture(browser,url,fixture,errors)
            await expect(page.locator('#comparisonStatus')).to_contain_text('different track labels',timeout=20000)
            assert await canvas_delta(page)==0
            checks['known_different_circuits_still_rejected']=True
            await context.close()
            assert not errors,errors
            await browser.close()
    finally:
        server.shutdown()
    result={'checks':checks,'page_errors':errors,'scope':'Private API fixtures; real comparison/context backend and canvas pixels. Sparse/singleton payloads are explicit frontend contract cases.'}
    (output/'report.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    asyncio.run(main())
