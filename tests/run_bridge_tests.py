#!/usr/bin/env python3
"""Run isolated Godot tests; optional --dialogic checkout validates real parser events."""
import contextlib
import argparse
import pathlib
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--godot', default='godot')
parser.add_argument('--dialogic', type=pathlib.Path)
parser.add_argument('--work-dir', type=pathlib.Path)
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
with (contextlib.nullcontext(str(args.work_dir)) if args.work_dir else tempfile.TemporaryDirectory(prefix='arrow-dialogic-')) as temporary:
    work = pathlib.Path(temporary)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / 'addons/arrow_dialogic', work / 'addons/arrow_dialogic', dirs_exist_ok=True)
    shutil.copy(root / 'tests/bridge/project.godot', work / 'project.godot')
    shutil.copy(root / 'tests/bridge/test_compiler.gd', work / 'test_compiler.gd')
    shutil.copy(root / 'examples/dialogic_bridge/guild_briefing.arrow', work / 'example.arrow')
    def run(*extra):
        result = subprocess.run([args.godot, '--headless', '--path', str(work), *extra], capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        (work / ('import.log' if '--editor' in extra else 'test.log')).write_text(output)
        if result.returncode or 'SCRIPT ERROR:' in output:
            print(output[-18000:])
            raise SystemExit(result.returncode or 1)
        print('\n'.join(line for line in output.splitlines() if 'Bridge compiler:' in line or 'Dialogic Alpha' in line))
    run('--script', 'res://test_compiler.gd')
    if args.dialogic:
        shutil.copytree(args.dialogic / 'addons/dialogic', work / 'addons/dialogic', dirs_exist_ok=True)
        shutil.copy(root / 'tests/bridge/test_dialogic.gd', work / 'test_dialogic.gd')
        (work / 'rend.tres').write_text('[gd_resource type="Resource" script_class="DialogicCharacter" load_steps=2 format=3]\n[ext_resource type="Script" path="res://addons/dialogic/Resources/character.gd" id="1"]\n[resource]\nscript = ExtResource("1")\ndisplay_name = "Rend"\n')
        with (work / 'project.godot').open('a') as stream:
            stream.write('\n[autoload]\nDialogic="*res://addons/dialogic/Core/DialogicGameHandler.gd"\n')
        run('--editor', '--import')
        run('--script', 'res://test_dialogic.gd')
        driver = work / 'addons/bridge_test'
        driver.mkdir(parents=True, exist_ok=True)
        shutil.copy(root / 'tests/bridge/test_importer.gd', driver / 'test_importer.gd')
        (driver / 'plugin.cfg').write_text('[plugin]\nname="Importer smoke test"\ndescription="Test only"\nauthor="Tests"\nversion="1"\nscript="test_importer.gd"\n')
        with (work / 'project.godot').open('a') as stream:
            stream.write('\n[editor_plugins]\nenabled=PackedStringArray("res://addons/dialogic/plugin.cfg", "res://addons/bridge_test/plugin.cfg")\n')
        run('--editor', '--quit-after', '600')
