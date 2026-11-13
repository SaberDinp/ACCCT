"""Read-only real-recording regression for track and paired-frame review.

Lap boundaries are test inputs; positions are estimates, not a measured racing line.
Only read-only comparison/trace/inference POSTs are allowed. Saved recordings are untouched.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def canvas_pixels(page):
    return page.locator("#trackMap").evaluate("""canvas => {
      const data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
      const colors = {centerline:[116,139,128],current_pace:[109,204,223],reference_pace:[224,191,121]};
      return Object.fromEntries(Object.entries(colors).map(([name,color]) => {
        let count=0;
        for(let i=0;i<data.length;i+=4) {
          if(data[i+3]>180 && Math.hypot(data[i]-color[0],data[i+1]-color[1],data[i+2]-color[2])<12) count++;
        }
        return [name,count];
      }));
    }""")


def click_track_point(page, point):
    canvas = page.locator("#trackMap")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    size = min(box["width"], box["height"]) - 40
    canvas.click(position={"x": (box["width"] - size) / 2 + point["x"] * size,
                           "y": (box["height"] - size) / 2 + point["y"] * size})


def seconds(label):
    minute, second = label.split(":")
    return int(minute) * 60 + float(second)


def wait_comparison(page):
    page.wait_for_function("document.getElementById('comparisonStatus').textContent === ''", timeout=30000)
    expect(page.locator("#runComparison")).to_be_enabled()
    expect(page.locator("#runComparison")).to_have_attribute('aria-busy', 'false')


def seek_distance(page, value):
    page.locator("#trackReviewDistance").fill(str(round(value)))
    page.locator("#trackReviewDistance").dispatch_event("input")


def assert_source_frames(page, comparison, frame_requests, sessions):
    page.wait_for_function("""() => ['current','reference'].every(side => {
      const image=document.getElementById(`track${side}Image`);
      const video=document.getElementById(`track${side}Native`);
      return (!video.hidden && video.readyState>=2 && video.videoWidth>100 && !video.seeking) ||
        (!image.hidden && image.complete && image.naturalWidth>100);
    })""", timeout=30000)
    position = float(page.locator("#trackReviewDistance").input_value())
    point = min(comparison["points"], key=lambda item: abs(item["position"] - position))
    row = page.locator("#comparisonValues tr").filter(has_text="Source").locator("td").all_text_contents()
    result = {}
    for index, side in enumerate(("current", "reference"), 1):
        label = page.locator(f"#track{side}Time").inner_text()
        assert label == row[index], (side, label, row)
        stamp = seconds(label)
        assert abs(stamp - point[side]["time"]) < 0.011, (side, stamp, point[side]["time"])
        native = page.locator(f"#track{side}Native").is_visible()
        if native:
            page.wait_for_function("""([side,time]) => {
              const video=document.getElementById(`track${side}Native`);
              return !video.seeking && Math.abs(video.currentTime-time)<.1;
            }""", arg=[side, point[side]["time"]])
        else:
            assert any(request["session"] == sessions[side] and abs(request["time"] - point[side]["time"]) < .0005
                       for request in frame_requests), (side, point[side]["time"], frame_requests[-8:])
        pixels = page.locator(f"#track{side}{'Native' if native else 'Image'}").evaluate("""image => {
          const canvas=document.createElement('canvas'); canvas.width=96; canvas.height=54;
          const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0,96,54);
          const data=ctx.getImageData(0,0,96,54).data; const colors=new Set();
          let sum=0,squares=0;
          for(let i=0;i<data.length;i+=4) {
            colors.add(`${data[i]>>4},${data[i+1]>>4},${data[i+2]>>4}`);
            const brightness=(data[i]+data[i+1]+data[i+2])/3;
            sum+=brightness; squares+=brightness*brightness;
          }
          const n=data.length/4;
          return {width:image.naturalWidth || image.videoWidth,height:image.naturalHeight || image.videoHeight,
            colors:colors.size,variance:squares/n-(sum/n)**2};
        }""")
        assert pixels["colors"] > 30 and pixels["variance"] > 100, pixels
        result[side] = {"time": stamp, "source_time": point[side]["time"],
                        "medium": "native_video" if native else "frame_image", "pixels": pixels}
    return result


def assert_layout(page):
    measurement = page.evaluate("""() => {
      const root=document.documentElement;
      const offenders=[...document.querySelectorAll('#trackReview *')].filter(e=> {
        const r=e.getBoundingClientRect();
        return e.checkVisibility({visibilityProperty:true}) && r.width && r.height && (r.left < -1 || r.right > innerWidth+1);
      }).map(e=>e.id || e.className);
      const boxes=['trackSelect','trackReviewDistance','trackExpand']
        .map(id=> {const r=document.getElementById(id).getBoundingClientRect();
          return {id,x:r.x,y:r.y,width:r.width,height:r.height};});
      return {viewport:innerWidth,scrollWidth:root.scrollWidth,offenders,boxes};
    }""")
    assert measurement["scrollWidth"] <= measurement["viewport"] + 1, measurement
    assert not measurement["offenders"], measurement
    for index, a in enumerate(measurement["boxes"]):
        for b in measurement["boxes"][index + 1:]:
            assert not (a["x"] < b["x"] + b["width"] and a["x"] + a["width"] > b["x"] and
                        a["y"] < b["y"] + b["height"] and a["y"] + a["height"] > b["y"]), (a, b)
    return measurement


def lock_boundary(page, side, edge, time):
    page.locator(f"#{side}{edge}Boundary").click()
    page.locator("#alignmentTime").fill(str(time))
    page.locator("#alignmentTime").dispatch_event("input")
    expect(page.locator("#alignmentLock")).to_be_enabled(timeout=20000)
    page.locator("#alignmentLock").click()
    wait_comparison(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--current-session", default="0462ac78f63b4d078901d395ca77bac7")
    parser.add_argument("--reference-session", default="d296ac6b613944dc8bb0959c505a6615")
    parser.add_argument("--current-lap", default="lap-1")
    parser.add_argument("--reference-start", type=float, default=2.0)
    parser.add_argument("--reference-end", type=float, default=118.9)
    parser.add_argument("--track", default="silverstone", help="Circuit matching the selected recordings")
    parser.add_argument("--output", type=Path, default=Path("artifacts/track-review-real"))
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    errors, writes, blocked_writes, frame_requests = [], [], [], []
    result = {"current_session": args.current_session, "reference_session": args.reference_session,
              "position_kind": "Automatic lap progress estimate; not a measured racing line", "checks": {}, "screenshots": []}
    sessions = {"current": args.current_session, "reference": args.reference_session}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width":1440,"height":1050}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                writes.append({"method":request.method,"path":path})
                if request.method != "POST" or path not in {"/api/compare","/api/trace","/api/track-inference"}:
                    blocked_writes.append(writes[-1]);route.abort();return
            if path.endswith("/frame.jpg"):
                frame_requests.append({"session":path.split("/")[-2],
                    "time":float(parse_qs(urlparse(request.url).query)["time"][0])})
            route.continue_()
        context.route("**/*", guard)
        try:
            response = page.request.get(args.url+"/api/tracks")
            assert response.ok, response.status
            tracks = response.json()
            assert len(tracks)>=6
            before = page.request.get(args.url+"/api/status").json()
            saved = {side:hashlib.sha256(page.request.get(args.url+"/api/sessions/"+sid).body()).hexdigest()
                     for side,sid in sessions.items()}
            preferences = {'axis':'distance','alignment_mode':'raw','align_start':False,'align_end':False,
                'current':{'id':args.current_session,'range':args.current_lap},
                'reference':{'id':args.reference_session,'range':'custom','start':args.reference_start,'end':args.reference_end}}
            context.add_init_script('if (!localStorage.getItem("accc.comparison.v1")) localStorage.setItem("accc.comparison.v1", '+json.dumps(json.dumps(preferences))+');')
            page.goto(args.url)
            wait_comparison(page)
            for edge,value in [('Start',args.reference_start),('End',args.reference_end)]:
                lock_boundary(page,'reference',edge,value)
            with page.expect_response(lambda response: response.url.endswith('/api/compare') and response.request.method=='POST') as pending:
                page.locator('#runComparison').click()
            comparison = pending.value.json()
            assert pending.value.ok and comparison['axis']=='distance' and comparison['points']
            wait_comparison(page)
            expect(page.locator('#trackProgressMode,#trackManualControls,#trackSetOrigin,#trackReverse,#trackcurrentCar,#trackreferenceCar')).to_have_count(0)
            result['checks']['automatic_only_controls_and_no_car_names'] = True
            result['checks']['tracks'] = {}
            for track in tracks:
                data = page.request.get(args.url+'/api/tracks/'+track['id']).json()
                assert data['geometry_kind'].startswith('approximate_') and not data['racing_line_available']
                page.locator('#trackSelect').select_option(track['id'])
                expect(page.locator('#trackAnnotations')).to_have_attribute('data-track-id',track['id'])
                expect(page.locator('#trackOriginStatus')).to_have_text('Lap start / finish')
                pixels = canvas_pixels(page)
                assert sum(pixels.values())>400,pixels
                result['checks']['tracks'][track['id']]={'points':len(data['points']),'pixels':pixels}
            track = page.request.get(args.url+'/api/tracks/'+args.track).json()
            page.locator('#trackSelect').select_option(args.track)
            expect(page.locator('#trackAnnotations')).to_have_attribute('data-track-id',args.track)
            expect(page.locator('#trackCarMarker')).to_be_visible(timeout=20000)
            finish = next((line['progress'] for line in track['timing_lines'] if line['kind']=='start_finish'),0)
            target_fraction=(finish+.3)%1
            target=min(track['points'],key=lambda point:abs(point['distance_m']/track['length_m']-target_fraction))
            click_track_point(page,target)
            progress=float(page.locator('#trackCarMarker').get_attribute('data-progress'))
            assert abs(progress-.3)<.03,progress
            result['checks']['map_click_uses_automatic_lap_progress']={'progress':progress,'target':.3}
            result['checks']['paired_frames']=assert_source_frames(page,comparison,frame_requests,sessions)
            position=float(page.locator('#trackReviewDistance').input_value())
            page.locator('#trackMap').press('ArrowRight')
            after_key=float(page.locator('#trackReviewDistance').input_value())
            assert 15<=after_key-position<=35,(position,after_key)
            result['checks']['keyboard_seek']=True
            # Manual circuit choice and lap-boundary locks survive a fresh page.
            page.reload();wait_comparison(page)
            expect(page.locator('#trackSelect')).to_have_value(args.track,timeout=20000)
            expect(page.locator('#trackCarMarker')).to_be_visible()
            result['checks']['circuit_choice_and_automatic_progress_persist']=True
            seek_distance(page,min(1500,comparison['extent'][1]*.5))
            result['checks']['reloaded_frames']=assert_source_frames(page,comparison,frame_requests,sessions)
            for width,height in [(1440,1050),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                page.locator('#trackReview').scroll_into_view_if_needed()
                result['checks'][f'layout_{width}']=assert_layout(page)
                expect(page.locator('#trackCarMarker')).to_be_visible()
                name=f'track-review-{width}.png'
                page.locator('#trackReview').screenshot(path=str(args.output/name));result['screenshots'].append(name)
            page.set_viewport_size({'width':1440,'height':1050})
            page.locator('#trackreferenceInspect').click()
            expect(page.locator('#alignmentDialog')).to_be_visible()
            inspected=float(page.locator('#alignmentTime').input_value())
            expected=result['checks']['reloaded_frames']['reference']['source_time']
            assert abs(inspected-expected)<=.001,(inspected,expected)
            page.locator('#closeAlignment').click()
            result['checks']['inspect_paired_frame']={'time':inspected,'expected':expected}
            page.locator('#axisElapsed').click();wait_comparison(page)
            expect(page.locator('#trackReviewDistance')).to_be_enabled()
            expect(page.locator('#trackCarMarker')).to_be_visible()
            result['checks']['elapsed_axis_keeps_automatic_lap_progress']=True
            after=page.request.get(args.url+'/api/status').json()
            assert after.get('session_id')==before.get('session_id') and after.get('state')==before.get('state')
            for side,sid in sessions.items():
                assert hashlib.sha256(page.request.get(args.url+'/api/sessions/'+sid).body()).hexdigest()==saved[side]
            assert not blocked_writes,blocked_writes
            assert not errors,errors
            result.update(passed=True,source_controls_mutated=False,saved_sessions_unchanged=True)
        except Exception as error:
            result.update(passed=False,error=f'{type(error).__name__}: {error}')
            page.screenshot(path=str(args.output/'failure.png'),full_page=True)
            raise
        finally:
            result.update(page_errors=errors,writes=writes,blocked_writes=blocked_writes)
            (args.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result,indent=2));browser.close()


if __name__ == '__main__':
    main()
