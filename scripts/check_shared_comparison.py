"""Exercise an extracted shared comparison with file:// and no network."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import zipfile

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/kyalami-export-review'
CURRENT = 'ca63eba2e5c3424facbb3cb2ab40f4a3'
REFERENCE = 'bd19be43d36e4145a8f25eb2eae079d2'


async def run(archive):
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='accc-shared-check-') as extracted:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extracted)
        entry = Path(extracted) / archive.stem / 'index.html'
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={'width': 1512, 'height': 1120}, offline=True)
            page = await context.new_page()
            errors, network = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: network.append(request.url) if request.url.startswith(('http:', 'https:', 'ws:', 'wss:')) else None)
            await page.goto(entry.as_uri())
            await expect(page.locator('#currentStart')).to_have_value('167.934')
            await expect(page.locator('#currentEnd')).to_have_value('270.330')
            await expect(page.locator('#referenceStart')).to_have_value('3.401')
            await expect(page.locator('#referenceEnd')).to_have_value('103.506')
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=30000)
            await expect(page.locator('#trackSelect')).to_have_value('kyalami')
            await expect(page.locator('#trackReviewPlay')).to_be_enabled()
            await page.wait_for_function("['current','reference'].every(side => {const v=document.getElementById('track'+side+'Native');return v.readyState>=2 && !v.hidden && v.videoWidth===1920;})")
            assert await page.locator('#sharedFailure').is_hidden()
            assert await page.locator('#currentStart').get_attribute('readonly') is not None
            for selector in ('#openReferenceImport', '#currentDelete', '#currentSaveLapRange'):
                if await page.locator(selector).count():
                    assert await page.locator(selector).is_hidden(), selector
            initial = await page.evaluate("""() => ({
                current:document.querySelector('#trackcurrentNative').currentTime,
                reference:document.querySelector('#trackreferenceNative').currentTime,
                summary:document.querySelector('.compare-summary-line').innerText,
                pace:document.querySelector('#trackPaceStatus').innerText,
                map:document.querySelector('#trackOriginStatus').innerText
            })""")
            assert abs(initial['current'] - 167.934) < .1, initial
            assert abs(initial['reference'] - 3.401) < .1, initial
            await page.screenshot(path=str(OUT / 'desktop.png'), full_page=True)

            # Embedded adapter returns fresh copies and rejects changed ranges.
            invariants = await page.evaluate("""async () => {
              const data=window.SHARED_COMPARISON, adapter=window.ACCCSharedReview.adapter;
              const id=data.preferences.current.id, selection=data.selections[id];
              const request={method:'POST',body:JSON.stringify({session_id:id,range:selection,axis:'distance'})};
              const a=await adapter.api('/api/trace',request), b=await adapter.api('/api/trace',request);
              a.current.duration=-1;
              let refused=false;
              try {await adapter.api('/api/trace',{method:'POST',body:JSON.stringify({session_id:id,range:{...selection,start_time:selection.start_time+1},axis:'distance'})});} catch {refused=true;}
              return {clone:b.current.duration>0,refused,sessions:data.sessions.length};
            }""")
            assert invariants == {'clone': True, 'refused': True, 'sessions': 2}

            await page.locator('#axisElapsed').click()
            await expect(page.locator('#axisElapsed')).to_have_attribute('aria-pressed', 'true')
            await expect(page.locator('#comparisonStatus')).to_have_text('')
            await page.wait_for_function("document.querySelector('#trackReviewDistance').max < 110")
            await page.locator('#trackReviewDistance').evaluate("el => {el.value=20;el.dispatchEvent(new Event('input',{bubbles:true}));}")
            await page.wait_for_function("Math.abs(document.querySelector('#trackcurrentNative').currentTime-187.934)<.1 && Math.abs(document.querySelector('#trackreferenceNative').currentTime-23.401)<.1")
            before = await page.locator('#trackcurrentNative').evaluate('v=>v.currentTime')
            await page.locator('#trackReviewPlay').click()
            await page.wait_for_function("before => document.querySelector('#trackcurrentNative').currentTime > before+.5", arg=before)
            await page.locator('#trackReviewPlay').click()
            await expect(page.locator('#trackReviewPlay')).to_have_attribute('aria-label', 'Play comparison')
            playback = await page.locator('#trackcurrentNative').evaluate('v=>v.currentTime')
            await page.locator('#trackcurrentInspect').click()
            await expect(page.locator('#alignmentDialog')).to_be_visible()
            await page.wait_for_function("document.querySelector('#alignmentImage').readyState>=2 && document.querySelector('#alignmentFrameStamp').textContent.startsWith('Frame')")
            inspected = await page.locator('#alignmentImage').evaluate('v=>v.currentTime')
            assert abs(inspected-playback) < .2, (inspected, playback)
            await page.locator('#alignmentFrameForward').click()
            await page.locator('#closeAlignment').click()

            await page.locator('#axisDistance').click()
            await expect(page.locator('#comparisonStatus')).to_have_text('')
            await page.wait_for_function("document.querySelector('#trackReviewDistance').max > 4000")
            await page.locator('#compareZoomIn').click()
            await page.locator('#compareZoomReset').click()
            await page.locator('#swapComparisonRuns').click()
            await expect(page.locator('#currentSession')).to_have_value(REFERENCE)
            await expect(page.locator('#referenceSession')).to_have_value(CURRENT)
            await expect(page.locator('#comparisonStatus')).to_have_text('')
            await page.wait_for_function("Math.abs(document.querySelector('#trackcurrentNative').currentTime-3.401)<.1")
            await page.locator('#swapComparisonRuns').click()
            await expect(page.locator('#currentSession')).to_have_value(CURRENT)
            await expect(page.locator('#comparisonStatus')).to_have_text('')
            await page.locator('#referenceSession').select_option('')
            await expect(page.locator('#comparisonStatus')).to_have_text('')
            await expect(page.locator('#trackreferenceVideo')).to_be_hidden()
            await page.locator('#referenceSession').select_option(REFERENCE)
            await expect(page.locator('#comparisonStatus')).to_have_text('')
            await expect(page.locator('#trackreferenceVideo')).to_be_visible()
            await page.wait_for_function("!document.querySelector('#trackreferenceNative').hidden")
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.screenshot(path=str(OUT / 'mobile.png'), full_page=True)
            overflow = await page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
            assert not overflow, 'Horizontal overflow on mobile'
            assert not errors, errors
            assert not network, network
            report = {'passed': True, 'entry': 'file:// extracted ZIP/index.html', 'network_offline': True,
                      'network_requests': network, 'page_errors': errors, 'initial': initial,
                      'playback_advanced_seconds': playback-before, 'inspected_source_seconds': inspected,
                      'adapter_invariants': invariants, 'checks': ['exact lap ranges', 'both native videos',
                      'Time and Distance', 'synchronized seek', 'play/pause', 'frame inspector',
                      'chart zoom/reset', 'swap', 'single recording', 'map', 'mobile layout']}
            (OUT / 'browser-report.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2))
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', type=Path, default=ROOT / 'artifacts/accc-kyalami-amg-comparison.zip')
    asyncio.run(run(parser.parse_args().archive.resolve()))
