"""Download pinned public artifacts; verify LFS SHA256 or Git blob SHA1."""
import argparse
import hashlib
import json
import shutil
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def verified(path, item):
    if not path.is_file() or path.stat().st_size != item['size']:
        return False
    expected = item.get('sha256') or item['blob_id']
    digest = hashlib.sha256() if item.get('sha256') else hashlib.sha1()
    if not item.get('sha256'):
        digest.update(f"blob {item['size']}\0".encode())
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest() == expected


def download(item):
    path = (ROOT / item['path']).resolve()
    if not path.is_relative_to((ROOT / 'models').resolve()):
        raise ValueError('Model destination must stay under models/')
    if verified(path, item):
        print('Verified', item['path'], flush=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    offset = part.stat().st_size if part.exists() else 0
    if offset >= item['size']:
        if verified(part, item):
            part.replace(path)
            return
        part.unlink()
        offset = 0
    if shutil.disk_usage(path.parent).free < item['size'] - offset + 5 * 1024**3:
        raise OSError('Insufficient disk space; leave at least 5 GiB free')
    endpoint = os.environ.get('HF_ENDPOINT', 'https://huggingface.co').rstrip('/')
    url = f"{endpoint}/{item['repo']}/resolve/{item['revision']}/{item['file']}"
    headers = {'Range': f'bytes={offset}-'} if offset else {}
    with requests.get(url, headers=headers, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        if offset and response.status_code == 200:
            offset = 0
        elif response.status_code == 206:
            if not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                raise OSError('Unexpected Content-Range; partial file preserved')
        with part.open('ab' if offset else 'wb') as out:
            for chunk in response.iter_content(8 * 1024**2):
                out.write(chunk)
    if not verified(part, item):
        # A corrupt partial file must not be resumed forever.
        part.unlink()
        raise OSError('Model integrity check failed: ' + item['path'])
    part.replace(path)
    print('Downloaded and verified', item['path'], flush=True)


def download_xet(item):
    from huggingface_hub import hf_hub_download
    path = (ROOT / item['path']).resolve()
    if not path.is_relative_to((ROOT / 'models').resolve()):
        raise ValueError('Model destination must stay under models/')
    if verified(path, item):
        print('Verified', item['path'], flush=True)
        return
    cache = ROOT / 'models/.downloads' / item['repo'].replace('/', '--')
    try:
        cached = Path(hf_hub_download(item['repo'], item['file'], revision=item['revision'], local_dir=cache))
    except Exception as error:
        raise OSError(f'Hub download failed for {item["path"]}: {error}') from error
    if not verified(cached, item):
        cached.unlink()
        raise OSError('Model integrity check failed: ' + item['path'])
    path.parent.mkdir(parents=True, exist_ok=True)
    cached.replace(path)
    path.with_suffix(path.suffix + '.part').unlink(missing_ok=True)
    print('Downloaded and verified', item['path'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['h3', 'production', 'tts', 'asr', 'all'], default='h3')
    parser.add_argument('--list', action='store_true', help='Inspect size and paths without downloading')
    parser.add_argument('--jobs', type=int, default=1, choices=range(1, 9), help='Concurrent independent file downloads')
    parser.add_argument('--retries', type=int, default=4)
    parser.add_argument('--transport', choices=['http', 'xet'], default='http')
    args = parser.parse_args()
    items = json.loads((ROOT / 'model-manifests/models.json').read_text())
    selected = [i for i in items if args.group == 'all' or i['group'] == args.group]
    print(f"{len(selected)} files, {sum(i['size'] for i in selected) / 1024**3:.1f} GiB")
    if args.list:
        for item in selected:
            print(item['path'])
        return
    def fetch(item):
        for attempt in range(args.retries + 1):
            try:
                return (download_xet if args.transport == 'xet' else download)(item)
            except (requests.RequestException, OSError) as error:
                if attempt == args.retries:
                    raise
                print(f"Retry {attempt+1}: {item['path']}: {error}", flush=True)
                time.sleep(min(30, 2 ** attempt))
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(fetch, selected))
    (ROOT / 'runtime').mkdir(exist_ok=True)
    if args.group in ['h3', 'all']:
        (ROOT / 'runtime/h3-models.status').write_text('complete\n')
    if args.group in ['production', 'all']:
        for name in ['models', 'primary']:
            (ROOT / 'runtime' / (name + '.status')).write_text('complete\n')


if __name__ == '__main__':
    main()
