"""Browser playback links use saved sources and a session-bound local player."""
import re

from fastapi.testclient import TestClient
import pytest

from main import create_app
from tests.test_engine import make_video


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(root=tmp_path)) as value:
        yield value


def recording(client, **source):
    engine = client.app.state.engine
    path = engine.root / 'recording.mp4'
    make_video(path)
    identifier = engine.store.create_session({'kind': 'file', 'path': str(path), **source})
    engine.store.save_summary(identifier, {}, status='completed')
    return identifier, path


def test_original_public_url_opens_without_downloading_or_requiring_local_file(client, monkeypatch):
    identifier, path = recording(client, original_url='https://www.youtube.com/watch?v=abcdefghijk')
    path.unlink()
    def unexpected(*_):
        raise AssertionError('External playback must not read/download local media')
    monkeypatch.setattr(client.app.state.engine, 'open_video_media', unexpected)
    assert client.get(f'/api/sessions/{identifier}/watch').json() == {
        'url': 'https://www.youtube.com/watch?v=abcdefghijk', 'kind': 'external', 'available': True}
    assert client.app.state.imports.worker is None


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///private/movie.mp4',
    'https://user:password@example.com/video', '//example.com/video', 'https://example.com:bad/video',
    'https://example.com\\@evil.example/video', 'https://example.com/\nvideo', 'https://example.com/ video'])
def test_untrusted_original_urls_fall_back_to_session_local_player(client, url):
    identifier, _ = recording(client, original_url=url)
    assert client.get(f'/api/sessions/{identifier}/watch').json() == {
        'url': f'/api/sessions/{identifier}/video/watch', 'kind': 'local', 'available': True}


def test_local_watch_has_controls_sound_safe_title_and_existing_byte_range_video(client, monkeypatch):
    title = 'Lap </h1><script>alert("bad")</script>'
    identifier, path = recording(client, name=title)
    def unexpected(*_):
        raise AssertionError('GET watch must not prepare audio or analyze media')
    monkeypatch.setattr(client.app.state.review_audio, 'prepare', unexpected)
    link = client.get(f'/api/sessions/{identifier}/watch').json()
    response = client.get(link['url'], params={'url': 'https://evil.example/video', 'path': '/private/movie.mp4'})
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/html')
    assert 'Lap &lt;/h1&gt;&lt;script&gt;' in response.text
    assert '<script>alert(' not in response.text
    video = re.search(r'<video\b[^>]*>', response.text).group()
    assert 'controls' in video and 'muted' not in video
    assert f'src="/api/sessions/{identifier}/video/media"' in video
    assert str(path) not in response.text and 'evil.example' not in response.text and '/private' not in response.text
    assert f'/api/sessions/{identifier}/audio' in response.text
    assert 'Use compatible sound' in response.text
    nonce = re.search(r'<script nonce="([^"]+)"', response.text).group(1)
    assert f"script-src 'nonce-{nonce}'" in response.headers['content-security-policy']
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-frame-options'] == 'DENY'
    media = client.get(f'/api/sessions/{identifier}/video/media', headers={'Range': 'bytes=0-31'})
    assert media.status_code == 206 and media.content == path.read_bytes()[:32]
    assert client.app.state.review_audio.worker is None


def test_missing_deleted_and_outside_recordings_cannot_be_exposed(client, tmp_path):
    identifier, path = recording(client)
    path.unlink()
    assert client.get(f'/api/sessions/{identifier}/watch').json() == {'url': None, 'kind': 'local', 'available': False}
    assert client.get(f'/api/sessions/{identifier}/video/watch').status_code == 404
    client.app.state.engine.store.delete_session(identifier)
    assert client.get(f'/api/sessions/{identifier}/watch').status_code == 404
    outside = tmp_path.parent / (tmp_path.name + '-outside.mp4')
    make_video(outside)
    try:
        identifier = client.app.state.engine.store.create_session({'kind': 'file', 'path': str(outside)})
        assert not client.get(f'/api/sessions/{identifier}/watch').json()['available']
        assert client.get(f'/api/sessions/{identifier}/video/watch').status_code == 404
    finally:
        outside.unlink()


def test_watch_stays_local_and_does_not_accept_remote_client_control(tmp_path):
    with TestClient(create_app(root=tmp_path), client=('192.0.2.10', 50000)) as client:
        identifier, _ = recording(client)
        assert client.get(f'/api/sessions/{identifier}/watch').status_code == 403
        assert client.get(f'/api/sessions/{identifier}/video/watch').status_code == 403
