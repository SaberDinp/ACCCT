"""Export a fixed Analysis comparison as an offline, double-clickable ZIP.

The production renderer uses precomputed production comparison results. This
build tool and the application/database are deliberately outside the ZIP.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen
import zipfile

import av

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from telemetry.comparison import compare_session_ranges, trace_session_range
from telemetry.session_context import enrich_comparison, session_context

TEMPLATE = ROOT / 'export_templates/comparison'
spec = importlib.util.spec_from_file_location('shared_transform', TEMPLATE / 'transform.py')
transform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transform)


def get(api, route, body=None):
    request = Request(api.rstrip('/') + route, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def media_info(path):
    with av.open(str(path)) as source:
        stream = source.streams.video[0]
        return {'duration': float(stream.duration * stream.time_base),
                'source_start': float((stream.start_time or 0) * stream.time_base),
                'width': stream.width, 'height': stream.height,
                'fps': float(stream.average_rate), 'video_codec': stream.codec_context.name}


def package_video(source, destination, review):
    original = media_info(source)
    if original['video_codec'] == 'h264':
        shutil.copyfile(source, destination)
    else:
        # Use H.264 so browsers do not need VP9-in-MP4 support. Keep the whole
        # source timeline: every renderer seek uses the original timestamps.
        valid = False
        if destination.exists():
            try:
                existing = media_info(destination)
                valid = existing['video_codec'] == 'h264' and abs(existing['duration'] - original['duration']) < .1
            except av.error.FFmpegError:
                pass
        if not valid:
            ffmpeg = shutil.which('ffmpeg') or str(ROOT / 'artifacts/racer-demo-source/tools/ffmpeg')
            staged = destination.with_suffix('.staged.mp4')
            command = [ffmpeg, '-hide_banner', '-y', '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?',
                       '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
                       '-threads', '4', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(staged)]
            with (review / 'reference-encode.log').open('w') as log:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
            staged.replace(destination)
    exported = media_info(destination)
    assert exported['source_start'] == original['source_start'] == 0, 'Export requires a zero-origin source video.'
    assert abs(exported['duration'] - original['duration']) < .1
    assert (exported['width'], exported['height']) == (original['width'], original['height'])
    return exported


def sanitized_detail(detail, media_url, track_id, car_id):
    detail = deepcopy(detail)
    session = detail['session']
    source = session['source']
    session['source'] = {key: source[key] for key in ('kind', 'name', 'original_url', 'role') if key in source}
    session['source']['path'] = media_url
    metadata = session['metadata']
    # Retain review metadata, not extraction configuration or import provenance.
    session['metadata'] = {key: metadata[key] for key in ('measurement', 'calibration', 'sample_rate_limit_hz', 'source_span_seconds') if key in metadata}
    session['metadata']['telemetry_context'] = {'track_id': track_id, 'car_id': car_id}
    session['context'] = session_context(session)
    return detail


def export(args):
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    review = ROOT / 'artifacts/kyalami-export-review'
    review.mkdir(parents=True, exist_ok=True)
    for folder in ('media', 'vendor', 'licenses', 'credits'):
        (destination / folder).mkdir(exist_ok=True)
    ids = {'current': args.current, 'reference': args.reference}
    selections = {args.current: {'start_time': args.current_start, 'end_time': args.current_end},
                  args.reference: {'start_time': args.reference_start, 'end_time': args.reference_end}}
    assert args.current != args.reference
    assert all(value['end_time'] > value['start_time'] >= 0 for value in selections.values())
    details, media, originals = {}, {}, {}
    for side, session_id in ids.items():
        original = get(args.api, '/api/sessions/' + session_id)
        originals[session_id] = original
        url = f'media/{side}.mp4'
        info = package_video(Path(original['session']['source']['path']), destination / url, review)
        media[session_id] = {**info, 'state': 'ready', 'session_id': session_id, 'message': None,
                             'url': url, 'media_type': 'video/mp4'}
        assert selections[session_id]['end_time'] <= info['duration']
        details[session_id] = sanitized_detail(original, url, args.track, args.car)

    traces, comparisons, parity = {}, {}, []
    for session_id, detail in details.items():
        traces[session_id] = {}
        for axis in ('distance', 'elapsed'):
            result = trace_session_range(detail['samples'], selection=selections[session_id], axis=axis, max_points=1200)
            result['sessions'] = {'current': detail['session'], 'reference': None}
            traces[session_id][axis] = result
    for current, reference in ((args.current, args.reference), (args.reference, args.current)):
        for axis in ('distance', 'elapsed'):
            body = {'current_session_id': current, 'reference_session_id': reference,
                    'current_range': selections[current], 'reference_range': selections[reference],
                    'axis': axis, 'alignment_mode': 'raw', 'align_start': False, 'align_end': False,
                    'confirm_same_track': True, 'max_points': 1200}
            result = compare_session_ranges(details[reference]['samples'], details[current]['samples'],
                reference_range=selections[reference], current_range=selections[current], axis=axis,
                alignment_mode='raw', align_start=False, align_end=False, max_points=1200)
            # Require exact numeric parity with the running app, including all
            # raw traces, gaps, sampling, estimated delta and metrics.
            live = get(args.api, '/api/compare', body)
            for key, value in result.items():
                if key != 'warnings':
                    assert live[key] == value, f'Comparison mismatch: {axis} {key}'
            result['sessions'] = {'current': details[current]['session'], 'reference': details[reference]['session']}
            enrich_comparison(result, details[current]['session'], details[reference]['session'], True)
            key = f'{current}|{reference}|{axis}|raw'
            comparisons[key] = result
            parity.append({'key': key, 'exact_numeric_match': True, 'points': len(result['points'])})

    data = {'id': destination.name + '-v1', 'title': args.title,
            'preferences': {'current': {'id': args.current}, 'reference': {'id': args.reference},
                'axis': 'distance', 'alignment_mode': 'raw', 'align_start': False, 'align_end': False, 'confirm_same_track': True},
            'selections': selections, 'sessions': list(details.values()), 'media': media,
            'traces': traces, 'comparisons': comparisons,
            'tracks': [get(args.api, '/api/tracks/' + args.track)], 'catalog': get(args.api, '/api/catalog'),
            'credits': [
                {'text': 'Your run · Champagne Endurance Team · Mercedes-AMG GT3 Evo · Sprint/Endurance', 'url': 'https://www.youtube.com/watch?v=jXWG5452KF8'},
                {'text': 'Reference · Fri3d0lf · Mercedes-AMG GT3 Evo · Hotlap + FREE Setup', 'url': 'https://www.youtube.com/watch?v=XtvSVHHaxMs'},
                {'text': 'Circuit geometry · SpeedHQ / RaceIQ contributors · AGPL-3.0', 'url': 'https://github.com/SpeedHQ/RaceIQ'},
                {'text': 'Circuit annotations · Lovely Sim Racing · CC BY-NC-SA 4.0', 'url': 'https://github.com/Lovely-Sim-Racing/lovely-track-data'},
                {'text': 'Lucide icons · ISC license', 'url': 'licenses/LICENSE-lucide.txt'},
            ]}
    for name in ('index.html', 'offline.css', 'offline.js', 'README.txt'):
        shutil.copyfile(TEMPLATE / name, destination / name)
    for name in ('styles.css', 'comparison.css', 'track-review.css', 'track-pace.js'):
        shutil.copyfile(ROOT / 'web' / name, destination / name)
    (destination / 'comparison.js').write_text(transform.transform_comparison((ROOT / 'web/comparison.js').read_text()))
    (destination / 'track-review.js').write_text(transform.transform_track_review((ROOT / 'web/track-review.js').read_text()))
    shutil.copyfile(ROOT / 'web/vendor/lucide.min.js', destination / 'vendor/lucide.min.js')
    for source in (ROOT / 'web/vendor/LICENSE-lucide.txt',
                   ROOT / 'assets/tracks/LICENSE-RaceIQ-AGPL-3.0.txt',
                   ROOT / 'assets/tracks/LICENSE-Lovely-CC-BY-NC-SA-4.0.txt'):
        shutil.copyfile(source, destination / 'licenses' / source.name)
    for source in (ROOT / f'assets/tracks/raceiq/{args.track}-boundaries.json',
                   ROOT / f'assets/tracks/lovely/{args.track}.json'):
        shutil.copyfile(source, destination / 'credits' / source.name)
    encoded = json.dumps(data, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    assert '/Users/' not in encoded and str(ROOT) not in encoded, 'Private path in shared data.'
    (destination / 'data.js').write_text('window.SHARED_COMPARISON = ' + encoded + ';\n')
    timing = {side: {**selections[session_id], 'duration': round(selections[session_id]['end_time'] - selections[session_id]['start_time'], 6)} for side, session_id in ids.items()}
    timing['delta_seconds'] = round(timing['current']['duration'] - timing['reference']['duration'], 6)
    report = {'selections': timing, 'comparison_parity': parity, 'media': media, 'output': str(destination)}
    (review / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    package(destination, review)
    return report


def package(destination, review):
    entries = []
    for path in sorted(destination.rglob('*')):
        if not path.is_file() or path.name == 'manifest.json':
            continue
        assert path.suffix not in ('.py', '.sqlite', '.db', '.log'), path
        assert not any(part.startswith('.') for part in path.relative_to(destination).parts), path
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        entries.append({'path': path.relative_to(destination).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest})
    manifest = {'entry': 'index.html', 'static': True, 'backend_required': False,
                'files': entries, 'total_bytes': sum(entry['bytes'] for entry in entries)}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    archive = destination.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                bundle.write(path, destination.name + '/' + path.relative_to(destination).as_posix(),
                             compress_type=zipfile.ZIP_STORED if path.suffix == '.mp4' else zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        for entry in entries:
            with bundle.open(destination.name + '/' + entry['path']) as source:
                assert hashlib.file_digest(source, 'sha256').hexdigest() == entry['sha256']
    report = {'zip': str(archive), 'bytes': archive.stat().st_size, 'all_hashes_verified': True,
              'no_python_or_database': True, 'file_count': len(entries) + 1}
    (review / 'package-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    for side in ('current', 'reference'):
        parser.add_argument('--' + side, required=True)
        parser.add_argument('--' + side + '-start', type=float, required=True)
        parser.add_argument('--' + side + '-end', type=float, required=True)
    parser.add_argument('--track', default='kyalami')
    parser.add_argument('--car', default='mercedes_amg_gt3_evo')
    parser.add_argument('--title', default='Kyalami · Mercedes-AMG GT3 Evo')
    parser.add_argument('--output', default=str(ROOT / 'artifacts/accc-kyalami-amg-comparison'))
    export(parser.parse_args())
