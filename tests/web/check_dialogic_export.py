#!/usr/bin/env python3
"""Load and play a browser-downloaded ZIP in the real pinned Dialogic project."""
import argparse, os, re, shutil, subprocess, tempfile, zipfile
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('archive',type=Path)
parser.add_argument('--godot',default='godot')
parser.add_argument('--dialogic',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
assert subprocess.check_output(['git','-C',str(args.dialogic),'rev-parse','HEAD'],text=True).strip()=='e301e1bb5e62e6de22c6b7748c4372d0570bb4a7'
work=args.output.resolve(); work.mkdir(parents=True,exist_ok=False)
shutil.copytree(args.dialogic/'addons/dialogic',work/'addons/dialogic')
shutil.copytree(root/'addons/arrow_dialogic',work/'addons/arrow_dialogic')
shutil.copy(root/'tests/web/test_downloaded_dialogic.gd',work/'test.gd')
with zipfile.ZipFile(args.archive) as archive:
    for item in archive.infolist():
        path=Path(item.filename)
        assert not path.is_absolute() and '..' not in path.parts
        archive.extract(item,work)
(work/'project.godot').write_text('config_version=5\n[application]\nconfig/name="PWA export validation"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n[autoload]\nDialogic="*res://addons/dialogic/Core/DialogicGameHandler.gd"\n')
env=os.environ.copy()
for key in ('XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME'):env[key]=str(work/key)
for name,extra in [('import',['--editor','--import']),('playback',['--script','res://test.gd'])]:
    with (work/(name+'.log')).open('w') as output:
        result=subprocess.run([args.godot,'--headless','--path',str(work),*extra],env=env,stdout=output,stderr=subprocess.STDOUT,timeout=90)
    text=(work/(name+'.log')).read_text()
    assert result.returncode==0 and not re.search(r'(?m)^(SCRIPT ERROR:|ERROR:)',text),text[-14000:]
    if name=='playback':assert 'Downloaded PWA Dialogic export:' in text
print('PASS: downloaded PWA ZIP loads and plays both branches in pinned Dialogic')
