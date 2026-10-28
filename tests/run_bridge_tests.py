#!/usr/bin/env python3
"""Run isolated Godot tests; optional --dialogic checkout validates real parser events."""
import contextlib
import argparse
import pathlib
import os
import re
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--godot', default='godot')
parser.add_argument('--dialogic', type=pathlib.Path)
parser.add_argument('--work-dir', type=pathlib.Path)
parser.add_argument('--arrow', action='store_true', help='Also open the example and its inspectors in an isolated Arrow copy')
parser.add_argument('--visual', action='store_true', help='Render playback on an available display and save screenshots')
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
if args.arrow and args.work_dir and (args.work_dir.resolve() == root or root in args.work_dir.resolve().parents):
    parser.error('--work-dir must be outside the Arrow checkout when using --arrow')
if args.dialogic:
    revision = subprocess.check_output(['git', '-C', str(args.dialogic), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != 'e301e1bb5e62e6de22c6b7748c4372d0570bb4a7':
        parser.error('--dialogic must be checked out at the pinned Alpha 20 commit')
if args.work_dir:
    args.work_dir = args.work_dir.resolve()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.work_dir = pathlib.Path(tempfile.mkdtemp(prefix='run-', dir=args.work_dir))
    print('Test artifacts: ' + str(args.work_dir), flush=True)
with (contextlib.nullcontext(str(args.work_dir)) if args.work_dir else tempfile.TemporaryDirectory(prefix='arrow-dialogic-')) as temporary:
    work = pathlib.Path(temporary)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / 'addons/arrow_dialogic', work / 'addons/arrow_dialogic', dirs_exist_ok=True)
    shutil.copy(root / 'tests/bridge/project.godot', work / 'project.godot')
    shutil.copy(root / 'tests/bridge/test_compiler.gd', work / 'test_compiler.gd')
    shutil.copy(root / 'examples/dialogic_bridge/guild_briefing.arrow', work / 'example.arrow')
    run_environment = os.environ.copy()
    for variable, folder in [('XDG_DATA_HOME', '.user-data'), ('XDG_CONFIG_HOME', '.user-config'), ('XDG_CACHE_HOME', '.user-cache')]:
        run_environment[variable] = str(work / folder)
        (work / folder).mkdir()
    run_number = 0
    def run(*extra, expect=None, visual=False, project=None):
        global run_number
        run_number += 1
        log = work / f'{run_number:02d}-{"import" if "--editor" in extra else "test"}.log'
        with log.open('w') as stream:
            try:
                result = subprocess.run([args.godot, *(['--rendering-method', 'gl_compatibility', '--audio-driver', 'Dummy'] if visual else ['--headless']), '--path', str(project or work), *extra], stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=120, env=run_environment)
            except subprocess.TimeoutExpired:
                print(log.read_text()[-18000:])
                raise SystemExit('Godot timed out; see ' + str(log))
        output = log.read_text()
        if result.returncode or re.search(r'(?m)^(SCRIPT ERROR:|ERROR:)', output) or (expect and expect not in output):
            print(output[-18000:])
            raise SystemExit(result.returncode or 1)
        print('\n'.join(line for line in output.splitlines() if 'Bridge compiler:' in line or 'Dialogic Alpha' in line or 'Arrow example:' in line or 'Story package' in line or 'Story archives:' in line))
    run('--script', 'res://test_compiler.gd', expect='Bridge compiler:')
    shutil.copytree(root / 'examples/dialogic_bridge/story', work / 'story')
    shutil.copy(root / 'tests/bridge/test_story.gd', work / 'test_story.gd')
    run('--script', 'res://test_story.gd', expect='Story package:')
    (work / 'scripts/editor').mkdir(parents=True, exist_ok=True)
    shutil.copy(root / 'scripts/editor/story_archive.gd', work / 'scripts/editor/story_archive.gd')
    shutil.copy(root / 'tests/web/test_archives.gd', work / 'test_archives.gd')
    run('--script', 'res://test_archives.gd', expect='Story archives:')
    if args.arrow:
        arrow = work / 'arrow-editor'
        shutil.copytree(root, arrow, ignore=shutil.ignore_patterns('.git', '.godot', '.arrow.config', '*.log', '__pycache__'))
        run('--editor', '--import', project=arrow)
        run('--script', str(root / 'tests/bridge/test_arrow_example.gd'), '--config-dir', 'user://', project=arrow, visual=args.visual, expect='Arrow example:')
    if args.dialogic:
        shutil.copytree(args.dialogic / 'addons/dialogic', work / 'addons/dialogic', dirs_exist_ok=True)
        shutil.copy(root / 'tests/bridge/test_dialogic.gd', work / 'test_dialogic.gd')
        (work / 'rend.tres').write_text('[gd_resource type="Resource" script_class="DialogicCharacter" load_steps=2 format=3]\n[ext_resource type="Script" path="res://addons/dialogic/Resources/character.gd" id="1"]\n[resource]\nscript = ExtResource("1")\ndisplay_name = "Rend"\n')
        with (work / 'project.godot').open('a') as stream:
            stream.write('\n[autoload]\nDialogic="*res://addons/dialogic/Core/DialogicGameHandler.gd"\n')
        run('--editor', '--import')
        run('--script', 'res://test_dialogic.gd', expect='Dialogic Alpha 20 parser:')
        driver = work / 'addons/bridge_test'
        driver.mkdir(parents=True, exist_ok=True)
        shutil.copy(root / 'tests/bridge/test_importer.gd', driver / 'test_importer.gd')
        (driver / 'plugin.cfg').write_text('[plugin]\nname="Importer smoke test"\ndescription="Test only"\nauthor="Tests"\nversion="1"\nscript="test_importer.gd"\n')
        with (work / 'project.godot').open('a') as stream:
            stream.write('\n[editor_plugins]\nenabled=PackedStringArray("res://addons/dialogic/plugin.cfg", "res://addons/bridge_test/plugin.cfg")\n')
        run('--editor', '--quit-after', '600', expect='Dialogic Alpha 20 editor importer:')

        shutil.copy(root / 'tests/bridge/test_playback.gd', work / 'test_playback.gd')
        run('--script', 'res://test_playback.gd', expect='persistent state verified', visual=args.visual)

        shutil.copy(root / 'tests/bridge/test_story_importer.gd', driver / 'test_story_importer.gd')
        (driver / 'plugin.cfg').write_text((driver / 'plugin.cfg').read_text().replace('test_importer.gd', 'test_story_importer.gd'))
        run('--editor', '--quit-after', '600', expect='Story package importer:')
        shutil.copy(root / 'tests/bridge/test_story_playback.gd', work / 'test_story_playback.gd')
        run('--script', 'res://test_story_playback.gd', expect='direct chapter entry verified', visual=args.visual)
