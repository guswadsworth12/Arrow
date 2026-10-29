"""Real authenticated MCP HTTP -> story HTTP -> SQLite/native compiler integration."""
from contextlib import asynccontextmanager
import asyncio
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from services.story_service.client import Client, load_bundle
from services.story_service.model import ServiceError
from services.story_mcp.remote import JWTVerifier, RemoteConfig

ROOT = Path(__file__).resolve().parents[2]

def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        return listener.getsockname()[1]

class RemoteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='arrow-remote-')
        cls.work = Path(cls.temp.name)
        cls.key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.kid = 'test-key'
        cls.jwks_reads = 0
        cls.redirect = False
        class Issuer(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.jwks_reads += 1
                if cls.redirect:
                    self.send_response(302); self.send_header('Location','/unexpected');self.end_headers();return
                key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
                key.update(kid=cls.kid,alg='RS256',use='sig')
                body=json.dumps({'keys':[key]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
            def log_message(self,*args): pass
        cls.issuer_server=ThreadingHTTPServer(('127.0.0.1',0),Issuer)
        threading.Thread(target=cls.issuer_server.serve_forever,daemon=True).start()
        cls.issuer=f'http://127.0.0.1:{cls.issuer_server.server_port}/realms/arrow'
        cls.token=secrets.token_urlsafe(32)
        cls.api_url=f'http://127.0.0.1:{free_port()}'
        cls.url=f'http://127.0.0.1:{free_port()}'
        cls.env=os.environ.copy()
        cls.env.update(ARROW_STORY_API_TOKEN=cls.token,ARROW_STORY_DB=str(cls.work/'stories.sqlite3'),
            ARROW_STORY_GODOT=os.environ.get('ARROW_STORY_GODOT','godot'),ARROW_STORY_URL=cls.api_url,
            ARROW_MCP_PUBLIC_URL=cls.url+'/mcp',ARROW_MCP_ISSUER_URL=cls.issuer,ARROW_MCP_JWKS_URL=cls.issuer+'/certs',
            ARROW_MCP_ALLOWED_ORIGINS='["https://trusted.example"]',ARROW_MCP_KEYCLOAK_ROLES='true')
        cls.processes=[];cls.logs=[]
        cls.start(['-m','services.story_service','serve','--port',cls.api_url.rsplit(':',1)[1]],'api')
        cls.client=Client(cls.api_url,cls.token)
        cls.wait_health(cls.api_url)
        cls.start(['-m','services.story_mcp','--transport','streamable-http','--port',cls.url.rsplit(':',1)[1],'--allow-apply'],'mcp')
        cls.wait_health(cls.url)
        cls.bundle=load_bundle(ROOT/'examples/dialogic_bridge/story/guild_story.json')

    @classmethod
    def start(cls,args,name):
        log=(cls.work/(name+'.log')).open('w');cls.logs.append(log)
        cls.processes.append(subprocess.Popen([sys.executable,*args],cwd=ROOT,env=cls.env,stdout=log,stderr=subprocess.STDOUT))

    @classmethod
    def wait_health(cls,url):
        with httpx.Client() as client:
            for _ in range(150):
                try:
                    if client.get(url+'/health').status_code==200:return
                except httpx.HTTPError:pass
                time.sleep(.1)
        raise RuntimeError('Service startup failed: '+url)

    @classmethod
    def tearDownClass(cls):
        for process in cls.processes:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        for log in cls.logs:log.close()
        cls.issuer_server.shutdown();cls.issuer_server.server_close();cls.temp.cleanup()

    def jwt(self,role='arrow-editor',scope='arrow:read arrow:edit arrow:apply',**changes):
        claims={'iss':self.issuer,'aud':self.url+'/mcp','sub':'test-account','azp':'test-client',
                'iat':int(time.time()),'exp':int(time.time())+300,'scope':scope,'realm_access':{'roles':[role]}}
        claims.update(changes)
        return jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':self.kid})

    @asynccontextmanager
    async def session(self,token):
        async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=60) as client:
            async with streamable_http_client(self.url+'/mcp',http_client=client) as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize();yield session

    async def call(self,session,name,args=None):
        value=await session.call_tool(name,args or {})
        self.assertFalse(value.isError,str(value.content))
        return value.structuredContent or json.loads(value.content[0].text)

    async def fresh(self):
        bundle=copy.deepcopy(self.bundle);bundle['package']['id']='remote_'+uuid.uuid4().hex[:12]
        await asyncio.to_thread(self.client.request,'POST','/v1/stories',{'bundle':bundle,'message':'Non-canon remote test'})
        return bundle['package']['id']

    async def test_discovery_authentication_and_token_binding(self):
        async with httpx.AsyncClient() as client:
            response=await client.post(self.url+'/mcp',json={})
            self.assertEqual(response.status_code,401)
            self.assertIn('resource_metadata="'+self.url+'/.well-known/oauth-protected-resource/mcp"',response.headers['www-authenticate'])
            self.assertEqual(response.headers['cache-control'],'no-store')
            metadata=(await client.get(self.url+'/.well-known/oauth-protected-resource/mcp')).json()
            self.assertEqual(metadata['resource'],self.url+'/mcp')
            self.assertEqual(metadata['authorization_servers'],[self.issuer])
            self.assertEqual(metadata['scopes_supported'],['arrow:read','arrow:edit','arrow:apply'])
            for token in [self.token,'invalid',self.jwt(exp=int(time.time())-30),self.jwt(aud='https://wrong.example/mcp'),
                          self.jwt(iss='https://wrong.example/'),self.jwt(nbf=int(time.time())+300),self.jwt(sub='')]:
                response=await client.post(self.url+'/mcp',headers={'Authorization':'Bearer '+token},json={})
                self.assertEqual(response.status_code,401,response.text)
                self.assertNotIn(self.token,response.text)
            response=await client.post(self.url+'/mcp',headers={'Authorization':'Bearer '+self.jwt(role='no-access')},json={})
            self.assertEqual(response.status_code,403)

    async def test_forged_tokens_and_jwks_rotation_fail_closed(self):
        config=RemoteConfig(self.url+'/mcp',self.issuer,self.issuer+'/certs',keycloak_roles=True)
        verifier=JWTVerifier(config)
        valid=self.jwt()
        self.assertIsNotNone(await verifier.verify_token(valid))
        claims=jwt.decode(valid,options={'verify_signature':False})
        forged_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        forged=[jwt.encode(claims,forged_key,algorithm='RS256',headers={'kid':self.kid}),
                jwt.encode(claims,'attacker-controlled-secret-with-32-bytes',algorithm='HS256',headers={'kid':self.kid}),
                jwt.encode(claims,key=None,algorithm='none',headers={'kid':self.kid}),
                jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':'unknown','jku':'https://attacker.invalid/keys'})]
        for value in forged:
            self.assertIsNone(await verifier.verify_token(value))
        self.assertIsNone(await verifier.verify_token('x'*(16*1024+1)))
        cls=type(self);old_key,old_kid=cls.key,cls.kid
        try:
            cls.key,cls.kid=forged_key,'rotated-key'
            verifier.next_fetch=0
            self.assertIsNotNone(await verifier.verify_token(self.jwt()))
            self.assertIsNone(await verifier.verify_token(valid))
            cls.redirect=True
            verifier.expires=0;verifier.next_fetch=0
            before=cls.jwks_reads
            self.assertIsNone(await verifier.verify_token(self.jwt()))
            self.assertEqual(cls.jwks_reads-before,1)  # Never follows a redirected JWKS URL.
            self.assertIsNone(await verifier.verify_token(self.jwt()))
            self.assertEqual(cls.jwks_reads-before,1)  # Repeated bad requests cannot force refetches.
        finally:
            cls.key,cls.kid=old_key,old_kid;cls.redirect=False

    async def test_reader_cannot_write_editor_can_propose_committer_can_apply(self):
        story=await self.fresh()
        args={'story_id':story,'base_revision':1,'message':'Remote editor proposal','operations':[
            {'op':'update_node','document':'briefing','node':'2','data':{'content':'Edited over authenticated MCP HTTP.'}}]}
        async with self.session(self.jwt('arrow-reader')) as reader:
            tools=await reader.list_tools();self.assertEqual(len(tools.tools),10)
            self.assertEqual((await self.call(reader,'read_story',{'story_id':story}))['revision'],1)
            self.assertEqual((await self.call(reader,'story_head',{'story_id':story}))['revision'],1)
            contract=await reader.read_resource('arrow://story-contract');self.assertTrue(contract.contents)
            denied=await reader.call_tool('propose_story_edits',args)
            self.assertTrue(denied.isError);self.assertIn('arrow:edit',denied.content[0].text)
        async with self.session(self.jwt('arrow-editor')) as editor:
            proposal=await self.call(editor,'propose_story_edits',args)
            self.assertEqual(proposal['status'],'ready')
            denied=await editor.call_tool('apply_story_proposal',{'story_id':story,'proposal_id':proposal['id']})
            self.assertTrue(denied.isError);self.assertIn('arrow:apply',denied.content[0].text)
            denied=await editor.call_tool('commit_story_edits',args)  # Live commits publish, so they need apply too.
            self.assertTrue(denied.isError);self.assertIn('arrow:apply',denied.content[0].text)
            self.assertEqual((await self.call(editor,'read_story',{'story_id':story}))['revision'],1)
        async with self.session(self.jwt('arrow-committer')) as committer:
            commit={'story_id':story,'proposal_id':proposal['id']}
            result=await self.call(committer,'apply_story_proposal',commit)
            self.assertEqual(result['revision'],2)
            self.assertEqual(await self.call(committer,'apply_story_proposal',commit),result)
            old=await self.call(committer,'read_story',{'story_id':story,'revision':1});self.assertEqual(old['revision'],1)
            self.assertNotIn('Edited over authenticated',json.dumps(old))
            live=await self.call(committer,'commit_story_edits',{**args,'base_revision':2,'message':'Remote live edit','operations':[
                {'op':'update_node','document':'briefing','node':'2','data':{'content':'Published live over MCP HTTP.'}}]})
            self.assertEqual(live['revision'],3);self.assertTrue(live['diff'])
            changes=await self.call(committer,'story_changes',{'story_id':story,'from_revision':2})
            self.assertIn('Published live',json.dumps(changes))

    async def test_claimed_scopes_cannot_escalate_roles_and_roles_need_scopes(self):
        async with self.session(self.jwt('arrow-reader')) as session:
            result=await session.call_tool('apply_story_proposal',{'story_id':'test','proposal_id':'none'})
            self.assertTrue(result.isError);self.assertIn('insufficient_scope',result.content[0].text)
        async with self.session(self.jwt('arrow-committer',scope='arrow:read')) as session:
            result=await session.call_tool('propose_story_edits',{'story_id':'test','base_revision':1,'message':'Denied',
                'operations':[{'op':'set_character_name','key':'rend','value':'Denied'}]})
            self.assertTrue(result.isError);self.assertIn('insufficient_scope',result.content[0].text)

    async def test_multiple_callers_conflicts_invalid_proposals_and_restoration(self):
        story=await self.fresh()
        async with self.session(self.jwt('arrow-committer')) as one, self.session(self.jwt('arrow-committer',sub='second-user')) as two:
            async def propose(session,text):
                return await self.call(session,'propose_story_edits',{'story_id':story,'base_revision':1,'message':'Concurrent remote edit',
                    'operations':[{'op':'update_node','document':'briefing','node':'2','data':{'content':text}}]})
            first,second=await asyncio.gather(propose(one,'First remote writer.'),propose(two,'Second remote writer.'))
            await self.call(one,'apply_story_proposal',{'story_id':story,'proposal_id':first['id']})
            conflict=await two.call_tool('apply_story_proposal',{'story_id':story,'proposal_id':second['id']})
            self.assertTrue(conflict.isError);self.assertIn('revision_conflict',conflict.content[0].text)
            invalid=await self.call(one,'propose_story_edits',{'story_id':story,'base_revision':2,'message':'Reject markup',
                'operations':[{'op':'update_node','document':'briefing','node':'2','data':{'content':'[signal=invalid]'}}]})
            self.assertEqual(invalid['status'],'invalid')
            denied=await one.call_tool('apply_story_proposal',{'story_id':story,'proposal_id':invalid['id']})
            self.assertTrue(denied.isError)
            restore=await self.call(one,'propose_story_edits',{'story_id':story,'base_revision':2,'message':'Restore initial',
                'operations':[{'op':'restore_revision','revision':1}]})
            result=await self.call(one,'apply_story_proposal',{'story_id':story,'proposal_id':restore['id']})
            self.assertEqual(result['revision'],3)

    async def test_host_origin_validation_and_jwks_cache(self):
        async with httpx.AsyncClient(headers={'Authorization':'Bearer '+self.jwt()}) as client:
            bad=await client.post(self.url+'/mcp',headers={'Host':'attacker.invalid'},json={})
            self.assertEqual(bad.status_code,400)
            bad=await client.post(self.url+'/mcp',headers={'Origin':'https://attacker.invalid'},json={})
            self.assertEqual(bad.status_code,403)
        count=type(self).jwks_reads
        async with self.session(self.jwt()) as session:
            await self.call(session,'list_stories');await self.call(session,'list_stories')
        self.assertLessEqual(type(self).jwks_reads-count,1)

    def test_startup_configuration_requires_explicit_secure_urls(self):
        for kwargs in [dict(public_url='http://remote.example/mcp'),dict(public_url='https://public.example/'),
                       dict(issuer_url='http://remote.example/'),dict(jwks_url='https://user:secret@example.com/jwks'),
                       dict(allowed_origins=('https://trusted.example/path',))]:
            fields=dict(public_url='https://mcp.example/mcp',issuer_url='https://auth.example/realms/arrow',jwks_url='https://auth.example/certs')
            fields.update(kwargs)
            with self.assertRaises(ValueError):RemoteConfig(**fields)

if __name__=='__main__':unittest.main()
