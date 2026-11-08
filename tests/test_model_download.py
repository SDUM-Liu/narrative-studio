"""Offline checks for model integrity and safe resumption; no real weights."""
import hashlib
import pytest
from scripts import download_models as d


@pytest.mark.parametrize('lfs', [True, False])
def test_same_size_corruption_is_rejected(tmp_path, lfs):
    data = b'original model data'
    item = {'size': len(data)}
    if lfs:
        item['sha256'] = hashlib.sha256(data).hexdigest()
    else:
        item['blob_id'] = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    file = tmp_path / 'model'
    file.write_bytes(data)
    assert d.verified(file, item)
    file.write_bytes(b'x' * len(data))
    assert not d.verified(file, item)


def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'ROOT', tmp_path)
    path = tmp_path / 'models/fixture.safetensors'
    path.parent.mkdir()
    item = {'path': 'models/fixture.safetensors', 'size': 6,
            'sha256': hashlib.sha256(b'abcdef').hexdigest(),
            'repo': 'test/fixture', 'revision': 'pinned', 'file': 'fixture.safetensors'}
    path.with_suffix('.safetensors.part').write_bytes(b'abc')
    return path, item


class Response:
    status_code = 206
    headers = {'Content-Range': 'bytes 3-5/6'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield b'def'


def test_interrupted_download_resumes_and_verifies(tmp_path, monkeypatch):
    path, item = fixture(tmp_path, monkeypatch)
    def get(url, **kwargs):
        assert '/resolve/pinned/' in url
        assert kwargs['headers'] == {'Range': 'bytes=3-'}
        return Response()
    monkeypatch.setattr(d.requests, 'get', get)
    d.download(item)
    assert path.read_bytes() == b'abcdef'
    assert not path.with_suffix('.safetensors.part').exists()


def test_wrong_range_does_not_publish_corrupt_model(tmp_path, monkeypatch):
    path, item = fixture(tmp_path, monkeypatch)
    response = Response()
    response.headers = {'Content-Range': 'bytes 0-2/6'}
    monkeypatch.setattr(d.requests, 'get', lambda *args, **kwargs: response)
    with pytest.raises(OSError, match='Content-Range'):
        d.download(item)
    assert not path.exists()
    assert path.with_suffix('.safetensors.part').read_bytes() == b'abc'
