"""Read-only isolated render audit of every sourced ACC circuit annotation map."""
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw
from playwright.async_api import async_playwright, expect
from check_analysis_layout_browser import AnalysisFixtures, ROOT, SOCKET_FIXTURE, StaticHandler, CURRENT, REFERENCE
from telemetry.tracks import list_tracks, get_track


class MapFixtures(AnalysisFixtures):
    async def route(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/tracks/'):
            return await route.fulfill(json=get_track(path.rsplit('/',1)[-1]))
        return await super().route(route)


async def main():
    output = ROOT/'artifacts/track-annotation-review'
    output.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(StaticHandler,directory=str(ROOT/'web')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    results=[];errors=[]
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            page=await browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
            fixture=MapFixtures(ROOT/'artifacts/desktop-video/source-1080p.mp4')
            await page.route('**/api/**',fixture.route)
            await page.add_init_script(SOCKET_FIXTURE)
            preferences={'axis':'distance','current':{'id':CURRENT,'range':'custom','start':0,'end':7.8,'segment':'1'},'reference':{'id':''}}
            await page.add_init_script('localStorage.setItem("accc.comparison.v1",'+json.dumps(json.dumps(preferences))+');')
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await page.get_by_role('button',name='Analysis',exact=True).click()
            await expect(page.locator('#comparisonStatus')).to_have_text('',timeout=20000)
            expected=list_tracks()
            choices=await page.locator('#trackSelect option').evaluate_all('els=>els.map(e=>e.value).filter(Boolean)')
            assert set(choices)=={track['id'] for track in expected}
            for item in expected:
                track=get_track(item['id'])
                await page.locator('#trackSelect').select_option(item['id'])
                await expect(page.locator('#trackAnnotations')).to_have_attribute('data-track-id',item['id'])
                count=len(track['corners'])+len(track['straights'])+len(track['timing_lines'])
                await page.wait_for_function('n=>document.querySelectorAll(".track-annotation").length===n',arg=count)
                await expect(page.locator('.track-timing[data-kind=start_finish]')).to_have_count(1)
                await expect(page.locator('.track-timing[data-kind=sector]')).to_have_count(len(track['timing_lines'])-1)
                await page.locator('#trackMap').scroll_into_view_if_needed()
                await page.mouse.move(1,1)
                await page.locator('.track-map-stage').screenshot(path=str(output/(item['id']+'.png')))
                overlap=await page.locator('.track-annotation').evaluate_all('''els=>{
                  const rects=els.map(e=>({name:e.getAttribute('aria-label'),r:e.getBoundingClientRect()})); const pairs=[];
                  for(let i=0;i<rects.length;i++)for(let j=i+1;j<rects.length;j++){
                    const a=rects[i],b=rects[j],w=Math.min(a.r.right,b.r.right)-Math.max(a.r.left,b.r.left),h=Math.min(a.r.bottom,b.r.bottom)-Math.max(a.r.top,b.r.top);
                    if(w>2&&h>2&&w*h>Math.min(a.r.width*a.r.height,b.r.width*b.r.height)*.25)pairs.push([a.name,b.name]);
                  }return pairs;
                }''')
                results.append({'id':item['id'],'name':item['name'],'corners':len(track['corners']),'straights':len(track['straights']),
                    'timing_lines':len(track['timing_lines']),'crowded_label_pairs':overlap,'warnings':track['annotation_warnings']})
            await page.locator('#trackSelect').select_option('nurburgring_24h')
            await expect(page.locator('#trackAnnotations')).to_have_attribute('data-track-id','nurburgring_24h')
            await page.wait_for_function('n=>document.querySelectorAll(".track-corner").length===n',arg=len(get_track('nurburgring_24h')['corners']))
            await page.locator('#trackExpand').click()
            await page.wait_for_function('document.fullscreenElement?.classList.contains("track-map-stage")')
            await page.mouse.move(1,1)
            await page.locator('.track-map-stage').screenshot(path=str(output/'nurburgring-expanded.png'))
            await page.locator('#trackCollapse').click()
            await page.wait_for_function('!document.fullscreenElement')
            assert not errors,errors
            await browser.close()
    finally:server.shutdown()
    tiles=[]
    for item in results:
        im=Image.open(output/(item['id']+'.png')).convert('RGB');im.thumbnail((330,410))
        tile=Image.new('RGB',(350,455),'#101813');tile.paste(im,((350-im.width)//2,34))
        ImageDraw.Draw(tile).text((12,10),item['name'],fill='#edf4ef')
        tiles.append(tile)
    sheet=Image.new('RGB',(1750,455*((len(tiles)+4)//5)),'#101813')
    for index,tile in enumerate(tiles):sheet.paste(tile,((index%5)*350,(index//5)*455))
    sheet.save(output/'all-circuits.png')
    report={'tracks':results,'page_errors':errors,'scope':'All25 real backend maps and sourced annotations rendered through browser; private API fixtures and temporary static server.'}
    (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'tracks':len(results),'corners':sum(r['corners'] for r in results),'crowded_pairs':sum(len(r['crowded_label_pairs']) for r in results),'page_errors':errors},indent=2))


if __name__=='__main__':asyncio.run(main())
