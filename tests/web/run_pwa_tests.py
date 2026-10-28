#!/usr/bin/env python3
"""Exercise exported Godot WASM, real browser file dialogs/downloads, service and offline PWA."""
import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.story_service.client import Client, load_bundle
from services.story_service.model import ServiceError
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--web-root', type=Path, required=True, help='Export produced with tools/export_pwa.py --test-driver')
parser.add_argument('--artifacts', type=Path, required=True)
parser.add_argument('--service-python', default=sys.executable)
parser.add_argument('--godot', default='godot')
parser.add_argument('--chromium', help='Browser executable; omit for Playwright-installed Chromium')
parser.add_argument('--production-root', type=Path, help='Also boot the uninstrumented production PWA')
args = parser.parse_args()
work = args.artifacts.resolve()
work.mkdir(parents=True, exist_ok=False)
token = secrets.token_urlsafe(32)
console = []
class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
site = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietFiles, directory=str(args.web_root.resolve())))
threading.Thread(target=site.serve_forever, daemon=True).start()
origin = f'http://127.0.0.1:{site.server_port}'
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
url = f'http://127.0.0.1:{port}'
environment = os.environ.copy()
environment.update(ARROW_STORY_API_TOKEN=token, ARROW_STORY_DB=str(work/'stories.sqlite3'), ARROW_STORY_GODOT=args.godot,
                   ARROW_STORY_ALLOWED_ORIGINS=json.dumps([origin]), ARROW_STORY_WEB_ROOT=str(args.web_root.resolve()))
log = (work/'service.log').open('w')
service = subprocess.Popen([args.service_python, '-m', 'services.story_service', 'serve', '--port', str(port)],
                           cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
client = Client(url, token)
try:
    for _ in range(150):
        try:
            if client.request('GET', '/health')['status'] == 'ok': break
        except ServiceError: time.sleep(.1)
    else: raise RuntimeError('Service failed to start')
    with urllib.request.urlopen(url+'/app/index.html') as response:
        assert response.status == 200 and token.encode() not in response.read()
    request = urllib.request.Request(url+'/v1/stories', method='OPTIONS', headers={
        'Origin':'https://untrusted.invalid', 'Access-Control-Request-Method':'POST',
        'Access-Control-Request-Headers':'authorization,content-type'})
    try:
        urllib.request.urlopen(request)
        raise AssertionError('Untrusted CORS origin accepted')
    except urllib.error.HTTPError as error:
        assert error.code == 400 and error.headers.get('Access-Control-Allow-Origin') is None
    bundle = load_bundle(ROOT/'examples/dialogic_bridge/story/guild_story.json')
    bundle['package']['id'] = 'pwa_demo'
    fixture = work/'input.zip'
    with zipfile.ZipFile(fixture, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(bundle['manifest_path'], json.dumps(bundle['package']))
        for chapter, spec in bundle['package']['documents'].items():
            archive.writestr(spec['path'], json.dumps(bundle['documents'][chapter]))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True,
            args=['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader','--disable-dev-shm-usage'])
        context = browser.new_context(accept_downloads=True, viewport={'width':1280,'height':1000})
        page = context.new_page()
        page.on('console', lambda message: console.append(message.type+': '+message.text))
        page.on('pageerror', lambda error: console.append('PAGE ERROR: '+str(error)))
        page.goto(origin+'/index.html', wait_until='domcontentloaded')
        page.wait_for_function('!!window.ArrowTest', timeout=120000)
        def call(action, **values):
            return page.evaluate('data => Promise.race([ArrowTest.send(data), new Promise((_, reject) => setTimeout(() => reject(new Error("Godot command timeout")), 45000))])', {'action':action, **values})
        def download(name, action, **values):
            with page.expect_download(timeout=30000) as event:
                state = call(action, **values)
            item = event.value
            item.save_as(work/name)
            assert (work/name).stat().st_size > 0, state
            return state
        state = call('state')
        assert state['menu'] and state['token_empty'], state
        # Token-less connections are only for the PWA's own (Cloudflare Access) origin, not another service.
        state = call('connect', url=url, token='')
        assert 'sign-in' in state['status'], state
        state = call('connect', url=url, token='x'*40)
        assert '401' in state['status'], state
        call('connect', url=url, token=token)
        hostile = work/'hostile.zip'
        with zipfile.ZipFile(hostile, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('../escape.arrow', '{}')
        with page.expect_file_chooser() as event:
            call('choose_import')
        event.value.set_files(hostile)
        for _ in range(100):
            state = call('state')
            if 'ZIP paths' in state['status']:
                break
            page.wait_for_timeout(50)
        assert 'ZIP paths' in state['status'] and not state['story'], state
        assert client.request('GET','/v1/stories')['stories']==[]
        with page.expect_file_chooser() as event:
            call('choose_import')
        event.value.set_files(fixture)
        deadline = time.monotonic()+45
        while time.monotonic()<deadline:
            state = call('state')
            if state['story']=='pwa_demo': break
            page.wait_for_timeout(100)
        else: raise AssertionError(state)
        assert state['revision']==1, state
        download('sources.zip', 'story_export')
        with zipfile.ZipFile(work/'sources.zip') as archive:
            assert json.loads(archive.read(bundle['manifest_path'])) == bundle['package']
            assert json.loads(archive.read('briefing.arrow')) == bundle['documents']['briefing']
        state = call('open')
        assert state['local_id'] >= 0, state
        for fmt, suffix in [('full-copy','arrow'),('json','json'),('html','html'),('csv','csv')]:
            download('chapter.'+suffix, 'chapter_export', format=fmt)
        assert '<html' in (work/'chapter.html').read_text().lower()
        assert 'resources' in json.loads((work/'chapter.arrow').read_text())
        state = call('edit_propose')
        assert state['proposal_status']=='ready' and 'Edited in the PWA.' in state['review'], state
        assert client.request('GET','/v1/stories/pwa_demo')['revision']==1
        call('apply')
        call('load',story='pwa_demo')
        state = call('settings')
        assert state['proposal_status']=='ready', state
        page.screenshot(path=str(work/'pwa-review.png'))
        call('apply')
        state = call('load',story='pwa_demo')
        assert state['revision']==3
        head = client.request('GET','/v1/stories/pwa_demo')
        assert head['bundle']['package']['registry']['characters']['rend']['name']=='PWA Rend'
        assert head['bundle']['package']['registry']['variables']['request_accepted']['init'] is True
        assert head['bundle']['package']['links'][0]['to']['node']=='2'
        download('dialogic.zip','story_export',dialogic=True)
        with zipfile.ZipFile(work/'dialogic.zip') as archive:
            timeline = archive.read('dialogic_export/pwa_demo/story.dtl').decode()
            assert 'jump arrow_followup__2' in timeline and 'PWA Rend' not in timeline # identifiers, not display names
            manifest = json.loads(archive.read('dialogic_export/pwa_demo/manifest.json'))
            assert manifest['variable_defaults']['request_accepted'] is True
            assert archive.read('project-snippet.godot')
        state = call('edit_propose')
        assert 'revision_conflict' in state['review'], state
        assert client.request('GET','/v1/stories/pwa_demo')['revision']==3
        # Reload under the installed service worker; no token is restored.
        page.evaluate('navigator.serviceWorker.ready')
        page.wait_for_timeout(3000)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('!!window.ArrowTest', timeout=120000)
        state = call('state')
        assert state['token_empty'] and state['revision']==3 and state['bindings']>=1, state
        page.wait_for_timeout(3000)
        urls = page.evaluate('async () => (await Promise.all((await caches.keys()).map(async key => (await (await caches.open(key)).keys()).map(r=>r.url)))).flat()')
        assert any(u.endswith('index.wasm') for u in urls) and any(u.endswith('index.pck') for u in urls), urls
        assert not any('/v1/' in u for u in urls), urls
        await_storage = page.evaluate('(token) => [...Object.values(localStorage),...Object.values(sessionStorage)].some(v=>v.includes(token))',token)
        assert not await_storage
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('!!window.ArrowTest', timeout=120000)
        state = call('state')
        assert state['token_empty'] and state['revision']==3, state
        download('offline-sources.zip','story_export')
        download('offline-dialogic.zip','story_export',dialogic=True)
        call('reopen_local')
        download('offline-chapter.arrow','chapter_export',format='full-copy')
        assert json.loads((work/'offline-chapter.arrow').read_text())['resources']['nodes']['2']['data']['content']=='Edited in the PWA.'
        # A fresh chapter opened from cached sources must retain the original
        # service binding even before credentials are entered after reload.
        call('open')
        call('edit_only', content='Authored offline, then proposed after reconnect.')
        download('offline-new-chapter.arrow','chapter_export',format='full-copy')
        context.set_offline(False)
        call('connect', url=url, token=token)
        state = call('propose_current')
        assert state['proposal_status']=='ready' and 'Authored offline' in state['review'], state
        assert client.request('GET','/v1/stories/pwa_demo')['revision']==3
        # Redirects must not forward bearer credentials, even on the same host.
        seen=[]
        class Redirect(BaseHTTPRequestHandler):
            def do_OPTIONS(self):
                self.send_response(204); self.send_header('Access-Control-Allow-Origin',origin)
                self.send_header('Access-Control-Allow-Methods','GET'); self.send_header('Access-Control-Allow-Headers','authorization,content-type'); self.end_headers()
            def do_GET(self):
                seen.append(self.path); self.send_response(302); self.send_header('Access-Control-Allow-Origin',origin)
                self.send_header('Location','/forwarded'); self.end_headers()
            def log_message(self,*args): pass
        redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        threading.Thread(target=redirect.serve_forever,daemon=True).start()
        try:
            status=page.evaluate('data => new Promise(resolve => ArrowStoryWeb.request(data.url,"GET",data.token,"",(status)=>resolve(status)))',
                {'url':f'http://127.0.0.1:{redirect.server_port}/v1/stories','token':token})
            assert status==0 and seen==['/v1/stories'],seen
        finally: redirect.shutdown(); redirect.server_close()
        if args.production_root:
            production = ThreadingHTTPServer(('127.0.0.1',0),partial(QuietFiles,directory=str(args.production_root.resolve())))
            threading.Thread(target=production.serve_forever,daemon=True).start()
            try:
                clean = browser.new_page(viewport={'width':1280,'height':1000})
                clean.add_init_script('window.arrowBootLogs=[]; const originalLog=console.log; console.log=(...args)=>{window.arrowBootLogs.push(args.join(" "));originalLog(...args);};')
                clean.goto(f'http://127.0.0.1:{production.server_port}/index.html',wait_until='domcontentloaded')
                clean.wait_for_function('window.arrowBootLogs.some(line=>line.includes("Sandbox:"))',timeout=120000)
                assert clean.evaluate('typeof window.ArrowTest === "undefined" && typeof window.ArrowGodotCommand === "undefined"')
                clean.screenshot(path=str(work/'pwa-production.png'))
                clean.close()
            finally: production.shutdown(); production.server_close()
        browser.close()
    assert not any('SCRIPT ERROR:' in line or 'PAGE ERROR:' in line for line in console), '\n'.join(console[-50:])
    print('PASS: exported PWA, CORS/auth, real ZIP picker, graph edit, shared definitions/links, conflicts, all Arrow exports, Dialogic/source ZIP, persistence, offline reload/export and redirect isolation')
finally:
    (work/'browser.log').write_text('\n'.join(console).replace(token,'[redacted]'))
    service.terminate()
    try: service.wait(timeout=10)
    except subprocess.TimeoutExpired: service.kill(); service.wait()
    log.close(); site.shutdown(); site.server_close()
