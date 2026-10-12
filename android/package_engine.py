"""Build the exact Python/HTML engine bundle that is shipped with the Android APK."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

repo = Path(__file__).resolve().parent.parent
output = repo / 'android/app/src/main/assets/engine.zip'
output.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
    for directory in ('newal_mobile', 'ui'):
        for file in sorted((repo / directory).rglob('*')):
            if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                archive.write(file, file.relative_to(repo).as_posix())
if output.stat().st_size < 10_000:
    raise SystemExit('Engine bundle unexpectedly small')
print(f'Engine ZIP: {output} ({output.stat().st_size} bytes)')
