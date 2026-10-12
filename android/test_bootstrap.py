"""Isolated smoke test of APK->Termux Python bootstrap without Android APIs."""
import functools
import hashlib
import http.server
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request


class AndroidBootstrapTests(unittest.TestCase):
    def test_background_engine_installs_bundle_and_serves_authenticated_api(self):
        root = Path(__file__).resolve().parent.parent
        bundle = root / 'android/app/src/main/assets/engine.zip'
        if not bundle.is_file():
            self.skipTest('Run python android/package_engine.py first')
        script = (root / 'android/app/src/main/assets/bootstrap.py').read_text()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / 'engine.zip').write_bytes(bundle.read_bytes())
            server = http.server.ThreadingHTTPServer(
                ('127.0.0.1', 0), functools.partial(
                    http.server.SimpleHTTPRequestHandler, directory=str(directory)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            env = dict(os.environ, HOME=str(directory / 'termux-home'))
            (directory / 'termux-home').mkdir()
            token = 'test-' + 'x' * 48
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            url = f'http://127.0.0.1:{server.server_port}/engine.zip'
            child = subprocess.Popen([sys.executable, '-c', script, url, digest, token],
                                     env=env, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            try:
                result = None
                for _ in range(40):
                    time.sleep(0.25)
                    if child.poll() is not None:
                        break
                    try:
                        req = urllib.request.Request('http://127.0.0.1:8787/api/status',
                                                     headers={'Authorization': 'Bearer ' + token})
                        with urllib.request.urlopen(req, timeout=0.35) as response:
                            result = response.read().decode('utf-8')
                        break
                    except Exception:
                        pass
                self.assertIsNotNone(result, f'backend failed: {child.poll()}')
                self.assertIn('"version"', result)
                self.assertTrue((directory / 'termux-home/.m6/runtime' / digest[:24]
                                 / 'newal_mobile/__main__.py').is_file())
            finally:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
