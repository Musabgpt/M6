"""Copy the Python engine and the web UI into the Android project before Gradle builds the APK.

newal_mobile/ goes to app/src/main/python (compiled into the APK by Chaquopy);
ui/ goes to app/src/main/assets/ui (copied to app storage and served by the engine).
"""
import shutil
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
app = repo / 'android/app/src/main'
targets = {repo / 'newal_mobile': app / 'python/newal_mobile', repo / 'ui': app / 'assets/ui'}
ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
for source, target in targets.items():
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=ignore)
    count = sum(1 for f in target.rglob('*') if f.is_file())
    print(f'{source.name}: {count} files -> {target.relative_to(repo)}')
if not (app / 'python/newal_mobile/embedded.py').is_file() or not (app / 'assets/ui/index.html').is_file():
    raise SystemExit('engine or UI missing from the Android project')

# Legacy Termux-mode bundle, still served to Termux by older app builds and checked by older CI.
from zipfile import ZIP_DEFLATED, ZipFile

bundle = app / 'assets/engine.zip'
with ZipFile(bundle, 'w', compression=ZIP_DEFLATED) as archive:
    for directory in ('newal_mobile', 'ui'):
        for file in sorted((repo / directory).rglob('*')):
            if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                archive.write(file, file.relative_to(repo).as_posix())
print(f'Termux bundle: {bundle.relative_to(repo)} ({bundle.stat().st_size} bytes)')
