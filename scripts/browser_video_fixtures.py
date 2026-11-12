"""Video fixtures for recorded-analysis browser tests, independent of user recordings."""
import argparse
import asyncio
import functools
from fractions import Fraction
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import av
import numpy as np
from playwright.async_api import async_playwright, expect

from check_delete_swap_browser import Fixtures, CURRENT, REFERENCE, PROTECTED
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler


def make_clip(path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic moving imagery tests playback without distributing personal footage.
    with av.open(str(path), 'w') as output:
        target = output.add_stream('libx264', rate=30)
        target.width, target.height, target.pix_fmt = 1920, 1080, 'yuv420p'
        target.options = {'crf': '20', 'preset': 'ultrafast'}
        for index in range(240):
            pixels = np.zeros((1080, 1920, 3), dtype=np.uint8)
            pixels[:] = (20, 34, 42)
            left = (index * 10) % 1740
            pixels[180:900, left:left + 180] = (216, 238, 136)
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, 30)
            for packet in target.encode(frame):
                output.mux(packet)
        for packet in target.encode():
            output.mux(packet)


def make_offset_clip(source_path, path):
    if path.exists():
        return
    with av.open(str(source_path)) as source, av.open(str(path), 'w') as output:
        target = output.add_stream('libx264', rate=30)
        target.width, target.height, target.pix_fmt = 640, 360, 'yuv420p'
        target.options = {'crf': '20', 'preset': 'ultrafast'}
        for index, original in enumerate(source.decode(video=0)):
            frame = original.reformat(width=640, height=360, format='yuv420p')
            frame.pts, frame.time_base = 120 + index, Fraction(1, 30)
            for packet in target.encode(frame):
                output.mux(packet)
            if index == 29:
                break
        for packet in target.encode():
            output.mux(packet)


class VideoFixtures(Fixtures):
    def __init__(self, clip, offset_clip):
        super().__init__()
        self.media = clip.read_bytes()
        self.offset_media = offset_clip.read_bytes()
        self.video_requests, self.jpeg_requests = [], []
        self.failure = False
        self.decode_failures = set()
        self.next_video = None
        for data in self.sessions.values():
            first = data['samples'][:80]
            data['samples'] = first + [{**item, 'segment_id': 2} for item in first]
            data['session']['sample_count'] = len(data['samples'])
        self.sessions[PROTECTED]['samples'] = [{**sample, 'time': 4 + index / 30}
            for index, sample in enumerate(self.sessions[PROTECTED]['samples'][:30])]

    async def route(self, route):
        path = urlparse(route.request.url).path
        if path.endswith('.jpg'):
            self.jpeg_requests.append(path)
        if '/video' not in path:
            return await super().route(route)
        self.video_requests.append(path)
        sid = path.split('/')[3]
        if path.endswith('/media'):
            media = (b'Not a decodable video' if sid in self.decode_failures else
                     self.offset_media if sid == PROTECTED else self.media)
            headers = {'Accept-Ranges': 'bytes'}
            start, end, status = 0, len(media) - 1, 200
            value = route.request.headers.get('range')
            if value:
                a, b = value.removeprefix('bytes=').split('-', 1)
                start, end = int(a or 0), min(end, int(b)) if b else end
                status = 206
                headers['Content-Range'] = f'bytes {start}-{end}/{len(media)}'
            return await route.fulfill(body=media[start:end + 1], status=status,
                                       content_type='video/mp4', headers=headers)
        gate, self.next_video = self.next_video, None
        if gate:
            await gate.wait()
        unavailable = self.failure
        await route.fulfill(json={'session_id': sid, 'state': 'unavailable' if unavailable else 'ready',
            'url': f'/api/sessions/{sid}/video/media' + ('?broken=1' if sid in self.decode_failures else ''),
            'source_start': 4 if sid == PROTECTED else 0,
            'width': 640 if sid == PROTECTED else 1920, 'height': 360 if sid == PROTECTED else 1080,
            'fps': 30, 'duration': 1 if sid == PROTECTED else 8})
        if gate:
            gate.finished.set()
