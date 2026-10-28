#!/usr/bin/env python3
"""Download official pinned Godot + no-threads web template, verify SHA-512, extract only needed files."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

VERSION = '4.6.3-stable'
ASSETS = {
    'Godot_v4.6.3-stable_linux.x86_64.zip': 'a035258da32b77f966a5376f9fa29c30a6adde826a85ba918e1605bd1fc9823eba7d85f1dd5e748956bd2ba72827c0025ffa11bb82aec91128c407a2e723c99c',
    'Godot_v4.6.3-stable_export_templates.tpz': 'da606b61c10157844f8300172df374472665f95015495cb1a7cd132c40ede404faa96cc1016a4b9662db9909ddea69632c4948b2cd11163438dad4808881fb68',
}
parser = argparse.ArgumentParser()
parser.add_argument('--directory', type=Path, required=True)
args = parser.parse_args()
target = args.directory.resolve()
target.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='arrow-web-toolchain-') as directory:
    for asset, expected in ASSETS.items():
        path = Path(directory) / asset
        print('Downloading and verifying', asset, flush=True)
        with urllib.request.urlopen(f'https://github.com/godotengine/godot-builds/releases/download/{VERSION}/{asset}', timeout=120) as response, path.open('wb') as output:
            shutil.copyfileobj(response, output)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha512').hexdigest() != expected:
                raise SystemExit('Checksum mismatch: ' + asset)
        with zipfile.ZipFile(path) as archive:
            name = 'templates/web_nothreads_release.zip' if asset.endswith('.tpz') else 'Godot_v4.6.3-stable_linux.x86_64'
            (target / ('web_nothreads_release.zip' if asset.endswith('.tpz') else 'godot')).write_bytes(archive.read(name))
(target / 'godot').chmod(0o755)
print('Verified Godot 4.6.3 toolchain:', target)
