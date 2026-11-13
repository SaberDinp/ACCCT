"""Curated imports are explicit, deduplicated, durable and retryable."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import threading

from fastapi.testclient import TestClient
import pytest

from main import create_app
from telemetry import reference_library
from telemetry.vision import HUDExtractor
from tests.test_engine import make_video, observed_frame, wait_until


ENTRIES = [
    {'id': 'youtube:abcdefghijk', 'url': 'https://www.youtube.com/watch?v=abcdefghijk',
     'provider': 'youtube', 'title': 'Reference A', 'creator': {'name': 'Fixture'},
     'car_id': 'mclaren_720s_gt3_evo', 'track_id': 'silverstone', 'game_version': '1.10'},
    {'id': 'youtube:lmnopqrstuv', 'url': 'https://www.youtube.com/watch?v=lmnopqrstuv',
     'provider': 'youtube', 'title': 'Reference B', 'creator': {'name': 'Fixture'},
     'car_id': 'bmw_m4_gt3', 'track_id': 'spa'},
    {'id': 'youtube:ABCDEFGHIJK', 'url': 'https://www.youtube.com/watch?v=ABCDEFGHIJK',
     'provider': 'youtube', 'title': 'Reference C', 'creator': {'name': 'Fixture'},
     'car_id': 'mclaren_720s_gt3_evo', 'track_id': 'silverstone'},
]


@pytest.fixture
def library_catalog(monkeypatch):
    monkeypatch.setattr(reference_library, 'reference_catalog', lambda: {
        'version': 1, 'updated_at': '2026-11-13', 'references': copy.deepcopy(ENTRIES)})
    monkeypatch.setattr(HUDExtractor, 'extract', observed_frame)


@pytest.fixture
def client(tmp_path, library_catalog):
    with TestClient(create_app(root=tmp_path)) as value:
        yield value


def endpoint(index=0):
    return '/api/reference-library/' + ENTRIES[index]['id'] + '/import'


def downloader(client, monkeypatch, gate=None, failure=None):
    importer = client.app.state.imports
    original = client.app.state.engine.root / 'generated-source.mp4'
    if not original.exists():
        make_video(original)
    calls = []
    entered = threading.Event()

    def download(job_id, source, stop):
        calls.append(source['url'])
        entered.set()
        if gate:
            while not gate.wait(.01):
                importer._check(stop)
        if failure:
            raise ValueError(failure)
        folder = importer.folder / job_id
        folder.mkdir()
        path = folder / 'video.mp4'
        shutil.copyfile(original, path)
        return path, 'Generated reference'

    monkeypatch.setattr(importer, '_download_youtube', download)
    return calls, entered


def finished(client, reply):
    job_id = reply['job']['id']
    importer = client.app.state.imports
    wait_until(lambda: importer.get(job_id)['state'] in {'completed', 'error', 'cancelled'})
    importer.worker.join(timeout=3)
    return importer.get(job_id)


def test_filtering_is_read_only_and_keeps_multiple_video_choices(client, monkeypatch):
    calls, _ = downloader(client, monkeypatch)
    result = client.get('/api/reference-library').json()
    assert result['count'] == 3 and result['cars'] and result['tracks']
    assert all(entry['cache'] == {'state': 'not_cached', 'session_id': None, 'job_id': None} for entry in result['references'])
    selected = client.get('/api/reference-library', params={'car_id': 'mclaren_720s_gt3_evo', 'track_id': 'silverstone'}).json()
    assert selected['count'] == 2 and len({entry['url'] for entry in selected['references']}) == 2
    empty = client.get('/api/reference-library', params={'car_id': 'bmw_m4_gt3', 'track_id': 'silverstone'}).json()
    assert empty['references'] == []
    assert not calls and client.app.state.imports.worker is None


def test_only_explicit_curated_ids_are_accepted(client):
    assert client.post('/api/reference-library/unknown/import').status_code == 404
    for body in ({'url': ENTRIES[0]['url']}, {'role': 'current'}, {'path': '../private.mp4'}, {'context': {}}):
        assert client.post(endpoint(), json=body).status_code == 400
    assert client.post(endpoint(), json=[]).status_code == 422
    for name in ('car_id', 'track_id'):
        assert client.get('/api/reference-library', params={name: 'unknown'}).status_code == 400
    assert client.post(endpoint(), headers={'Origin': 'https://example.com'}).status_code == 403
    assert client.app.state.imports.worker is None


def test_concurrent_choice_reuses_one_job_then_ready_recording(client, monkeypatch):
    gate = threading.Event()
    calls, entered = downloader(client, monkeypatch, gate)
    try:
        first = client.post(endpoint()).json()
        assert entered.wait(2)
        assert first['state'] == 'importing' and not first['reused']
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda _: client.post(endpoint(), json={}).json(), range(12)))
        assert {reply['job']['id'] for reply in replies} == {first['job']['id']}
        assert all(reply['reused'] for reply in replies)
        assert len(calls) == 1
        cache = client.get('/api/reference-library').json()['references'][0]['cache']
        assert cache['state'] == 'importing' and cache['job_id'] == first['job']['id']
        assert client.post(endpoint(1)).status_code == 400
    finally:
        gate.set()
    job = finished(client, first)
    assert job['state'] == 'completed', job
    session = client.app.state.engine.store.get_session(job['session_id'])
    assert session['sample_count'] == 20
    assert session['source']['reference_library_id'] == ENTRIES[0]['id']
    assert session['source']['original_url'] == ENTRIES[0]['url']
    assert session['metadata']['reference_library'] == ENTRIES[0]
    assert session['metadata']['telemetry_context']['car_id'] == ENTRIES[0]['car_id']
    assert session['metadata']['telemetry_context']['track_id'] == ENTRIES[0]['track_id']
    ready = client.post(endpoint()).json()
    assert ready == {'reference_id': ENTRIES[0]['id'], 'state': 'ready', 'session_id': job['session_id'], 'job': None, 'reused': True}
    assert len(calls) == 1


def test_ready_reference_survives_restart_and_unpaginated_cache_lookup(tmp_path, library_catalog, monkeypatch):
    with TestClient(create_app(root=tmp_path)) as client:
        calls, _ = downloader(client, monkeypatch)
        job = finished(client, client.post(endpoint()).json())
        identifier = job['session_id']
        assert len(calls) == 1
        for index in range(55):
            client.app.state.engine.store.create_session({'kind': 'browser', 'name': f'Later {index}'})
        assert identifier not in {item['id'] for item in client.get('/api/sessions').json()}
        included = client.get('/api/sessions', params=[('include', identifier), ('include', identifier), ('include', 'missing')]).json()
        assert sum(item['id'] == identifier for item in included) == 1
        assert len(included) == 51
    with TestClient(create_app(root=tmp_path)) as restarted:
        def unexpected(*_):
            raise AssertionError('Ready reference must not download again')
        monkeypatch.setattr(restarted.app.state.imports, 'start', unexpected)
        assert restarted.post(endpoint()).json()['session_id'] == identifier
        cache = restarted.get('/api/reference-library').json()['references'][0]['cache']
        assert cache == {'state': 'ready', 'session_id': identifier, 'job_id': None}
        assert restarted.app.state.imports.worker is None


def test_missing_video_and_deleted_session_can_be_imported_again(client, monkeypatch):
    calls, _ = downloader(client, monkeypatch)
    first = finished(client, client.post(endpoint()).json())
    original = client.app.state.engine.store.get_session(first['session_id'])
    Path(original['source']['path']).unlink()
    assert client.get('/api/reference-library').json()['references'][0]['cache']['state'] == 'not_cached'
    second = finished(client, client.post(endpoint()).json())
    assert second['state'] == 'completed' and second['session_id'] != first['session_id']
    assert client.delete('/api/sessions/' + second['session_id']).status_code == 200
    third = finished(client, client.post(endpoint()).json())
    assert third['state'] == 'completed' and third['session_id'] not in {first['session_id'], second['session_id']}
    assert len(calls) == 3


def test_failed_and_cancelled_imports_remain_retryable(client, monkeypatch):
    downloader(client, monkeypatch, failure='Recording temporarily unavailable')
    failed = finished(client, client.post(endpoint()).json())
    assert failed['state'] == 'error'
    gate = threading.Event()
    _, entered = downloader(client, monkeypatch, gate)
    reply = client.post(endpoint()).json()
    assert entered.wait(2)
    client.post('/api/imports/' + reply['job']['id'] + '/cancel')
    cancelled = finished(client, reply)
    assert cancelled['state'] == 'cancelled'
    downloader(client, monkeypatch)
    final = finished(client, client.post(endpoint()).json())
    assert final['state'] == 'completed'
    assert len({failed['id'], cancelled['id'], final['id']}) == 3


def test_error_analysis_never_reused_as_completed_cache(client, monkeypatch):
    importer = client.app.state.imports
    broken = client.app.state.engine.root / 'broken.mp4'
    broken.write_bytes(b'not a video')
    monkeypatch.setattr(importer, '_download_youtube', lambda *_: (broken, 'Broken'))
    failed = finished(client, client.post(endpoint()).json())
    assert failed['state'] == 'error' and failed['session_id']
    assert client.app.state.engine.store.get_session(failed['session_id'])['status'] == 'error'
    downloader(client, monkeypatch)
    final = finished(client, client.post(endpoint()).json())
    assert final['state'] == 'completed' and final['session_id'] != failed['session_id']


def test_bundled_catalog_has_real_unique_urls_and_exact_roster_identities():
    data = reference_library.reference_catalog()
    assert len(data['references']) >= 100
    assert len({entry['id'] for entry in data['references']}) == len(data['references'])
    assert len({entry['track_id'] for entry in data['references']}) == 25
