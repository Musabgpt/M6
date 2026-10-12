"""Run by Termux's explicit RUN_COMMAND permission; not a remote shell endpoint.

Termux launches this code with its own Python interpreter. An M6-owned local
HTTP server supplies a pinned bundle; no GitHub download or credentials are
needed for the APK to install/update the bundled backend.
"""
import hashlib
import io
import os
from pathlib import Path
import secrets
import sys
import urllib.request
import zipfile


def main():
    if len(sys.argv) != 4:
        raise SystemExit('Expected archive URL, digest and bearer token')
    url, expected, token = sys.argv[1:]
    if not url.startswith('http://127.0.0.1:') or '/engine.zip' not in url:
        raise SystemExit('Refusing non-local bundle URL')
    if len(expected) != 64 or not all(c in '0123456789abcdef' for c in expected):
        raise SystemExit('Invalid SHA-256')
    state = Path.home() / '.m6'
    state.mkdir(mode=0o700, exist_ok=True)
    root = state / 'runtime'
    root.mkdir(exist_ok=True)
    target = root / expected[:24]
    marker = target / 'newal_mobile' / '__main__.py'
    if not marker.is_file():
        with urllib.request.urlopen(url, timeout=30) as response:
            archive = response.read(6_000_001)
        if len(archive) > 6_000_000 or hashlib.sha256(archive).hexdigest() != expected:
            raise SystemExit('Bundled engine SHA-256 verification failed')
        stage = root / ('.stage-' + secrets.token_hex(8))
        stage.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                for item in zf.infolist():
                    name = item.filename
                    parts = Path(name).parts
                    if not parts or parts[0] not in ('newal_mobile', 'ui') or any(
                        p in ('.', '..') for p in parts
                    ) or name.startswith('/'):
                        raise SystemExit('Invalid packaged path: ' + name)
                    if item.is_dir():
                        (stage / name).mkdir(parents=True, exist_ok=True)
                        continue
                    dest = stage / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(item) as src, dest.open('wb') as out:
                        out.write(src.read(6_000_000))
            if not (stage / 'newal_mobile' / '__main__.py').is_file():
                raise SystemExit('Bundled engine entrypoint missing')
            stage.rename(target)
        finally:
            if stage.exists():
                import shutil
                shutil.rmtree(stage)
    os.environ['NEWAL_TOKEN'] = token
    os.chdir(target)
    log = (state / 'engine.log').open('a', encoding='utf-8')
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    os.execv(sys.executable, [sys.executable, '-m', 'newal_mobile', '--port', '8787'])


main()
