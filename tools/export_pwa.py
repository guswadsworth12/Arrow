#!/usr/bin/env python3
"""Export the self-contained PWA in an isolated checkout copy, preserving source files."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--godot', default='godot')
parser.add_argument('--template', type=Path, help='Official matching web_nothreads_release.zip; omit for installed Godot templates')
parser.add_argument('--test-driver', action='store_true', help=argparse.SUPPRESS)
parser.add_argument('--output', type=Path, required=True, help='New output directory')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = args.output.resolve()
if output.exists() or output.is_symlink() or output == root or root in output.parents:
    parser.error('Choose a new output directory outside the source checkout')
if args.template:
    if not args.template.is_file():
        parser.error('Template file does not exist')
    with zipfile.ZipFile(args.template) as archive:
        if b'_emscripten_init_main_thread_js' in archive.read('godot.js'):
            parser.error('The PWA preset needs web_nothreads_release.zip, not a threaded web template')
output.mkdir(parents=True)
logs = output.parent / ("." + output.name + "-buildlogs")
logs.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='arrow-pwa-export-') as directory:
    work = Path(directory)
    project = work / 'arrow'
    shutil.copytree(root, project, ignore=shutil.ignore_patterns('.git', '.godot', '__pycache__', '.arrow.config', '.arrow-service', '*.sqlite3', '*.sqlite3-wal', '*.sqlite3-shm', '*.log'))
    if args.test_driver:
        shutil.copy(project / 'tests/web/browser_driver.gd', project / 'web_test_driver.gd')
        (project / 'web_test_driver.tscn').write_text('[gd_scene load_steps=3 format=3]\n[ext_resource type="PackedScene" path="res://main.tscn" id="1"]\n[ext_resource type="Script" path="res://web_test_driver.gd" id="2"]\n[node name="Main" instance=ExtResource("1")]\n[node name="WebTest" type="Node" parent="."]\nscript = ExtResource("2")\n')
        config = project / 'project.godot'
        config.write_text(config.read_text().replace('run/main_scene="res://main.tscn"', 'run/main_scene="res://web_test_driver.tscn"'))
    if args.template:
        preset = project / 'export_presets.cfg'
        before, after = preset.read_text().split('[preset.2.options]', 1)
        after = after.replace('custom_template/release=""', 'custom_template/release=' + json.dumps(str(args.template.resolve())), 1)
        preset.write_text(before + '[preset.2.options]' + after)
    environment = os.environ.copy()
    for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'):
        environment[name] = str(work / name)
    for extra in (['--editor', '--import'], ['--export-release', 'Web', str(output / 'index.html')]):
        result = subprocess.run([args.godot, '--headless', '--path', str(project), *extra], env=environment,
                                capture_output=True, text=True, timeout=180)
        log = result.stdout + result.stderr
        (logs / ('import.log' if '--editor' in extra else 'export.log')).write_text(log)
        if result.returncode or 'SCRIPT ERROR:' in log or '\nERROR:' in log:
            raise SystemExit(log[-14000:])
if not all((output / name).is_file() for name in ('index.html', 'index.js', 'index.wasm', 'index.pck', 'index.service.worker.js', 'index.manifest.json')):
    raise SystemExit('Incomplete PWA export; inspect export.log')
print('Exported PWA:', output)
