"""Read-only delta integration against two existing reviewed recordings.

Only GET and the read-only trace/compare POST endpoints are allowed.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    base = args.url.rstrip('/')
    before, preferences = {}, {'axis': 'distance', 'alignment_mode': 'raw', 'align_start': False, 'align_end': False, 'confirm_same_track': False}
    for side in ['current', 'reference']:
        sid = getattr(args, side)
        response = requests.get(f'{base}/api/sessions/{sid}', timeout=20)
        response.raise_for_status()
        before[sid] = hashlib.sha256(response.content).hexdigest()
        data = response.json(); metadata = data['session']['metadata']; defaults = metadata['comparison_defaults']
        lap = next(lap for lap in metadata['reviewed_laps'] if lap['id'] == defaults['range_id'])
        preferences[side] = {'id': sid, 'range': 'custom', 'start': lap['start_time'], 'end': lap['end_time'],
                             'segment': '', 'defaults_revision': defaults['revision']}
    errors, blocked, results = [], [], {}
    output = ROOT / 'artifacts/recorded-delta-review'
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1600, 'height': 1100})
        async def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not (request.method == 'POST' and path in {'/api/trace', '/api/compare'}):
                blocked.append([request.method, path])
                await route.abort()
            else:
                await route.continue_()
        await page.route('**/api/**', guard)
        await page.add_init_script('localStorage.setItem("accc.comparison.v1",' + json.dumps(json.dumps(preferences)) + ');')
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(base)
        await page.get_by_role('button', name='Analysis', exact=True).click()
        await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=30000)
        await page.locator('#comparisonAlignmentMode').select_option('raw')
        await page.locator('#confirmAlignment').uncheck()
        await page.locator('#confirmSameTrack').uncheck()
        for axis, button in [('distance', '#axisDistance'), ('elapsed', '#axisElapsed')]:
            await page.locator(button).click()
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=30000)
            async with page.expect_response(lambda response: response.url.endswith('/api/compare') and response.request.method == 'POST') as reply:
                await page.locator('#runComparison').click()
            response = await reply.value
            assert response.ok
            result = await response.json()
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=30000)
            assert result['axis'] == axis and not result['align_start']
            assert result['delta_available'] and result['delta_mode'] == 'approximate'
            finite = [point for point in result['points'] if point['delta_time_s'] is not None]
            assert len(finite) > 10
            canvas = page.locator('#synchronizedChart')
            await canvas.scroll_into_view_if_needed()
            pixels = await canvas.evaluate('''canvas => {
                const w=canvas.width,h=canvas.height,ctx=canvas.getContext('2d'),data=ctx.getImageData(0,0,w,h).data;
                const lane=(h-34)/6;let count=0;
                for(let y=Math.ceil(10+5*lane+22);y<h-24;y++)for(let x=60;x<w-15;x++){
                    const i=(y*w+x)*4;
                    if(Math.hypot(data[i]-213,data[i+1]-233,data[i+2]-145)<45)count++;
                }return count;
            }''')
            assert pixels > 10, pixels
            await canvas.screenshot(path=str(output / (axis + '.png')))
            results[axis] = {'delta_mode': result['delta_mode'], 'delta_basis': result['delta_basis'], 'finite_points': len(finite),
                             'last_delta_s': finite[-1]['delta_time_s'], 'delta_pixels': pixels}
        await browser.close()
    for sid, digest in before.items():
        response = requests.get(f'{base}/api/sessions/{sid}', timeout=20)
        response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest() == digest, 'Saved recording changed'
    assert not errors and not blocked, {'errors': errors, 'blocked': blocked}
    report = {'passed': True, 'results': results, 'saved_recordings_unchanged': True,
              'page_errors': errors, 'blocked_mutations': blocked,
              'scope': 'Existing real recordings; unconfirmed starts and unknown circuit labels; real server, no API mocks.'}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--current', default='0241bca2f4364903a11272f6ca70f724')
    parser.add_argument('--reference', default='58d21833cec0498d8f8a1d593c946d76')
    asyncio.run(main(parser.parse_args()))
