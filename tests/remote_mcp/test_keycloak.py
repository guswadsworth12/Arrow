"""Opt-in real Keycloak + official OAuth client PKCE + authenticated MCP integration."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit, urlencode

import httpx
import jwt
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata
from pydantic import AnyUrl
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('arrow_deploy',ROOT/'deploy/remote-mcp/prepare.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

class Storage:
    def __init__(self,redirect):
        self.tokens=None
        self.info=OAuthClientInformationFull(client_id='arrow-chatgpt',redirect_uris=[AnyUrl(redirect)],token_endpoint_auth_method='none',scope='openid arrow:read arrow:edit arrow:apply')
    async def get_tokens(self):return self.tokens
    async def set_tokens(self,value):self.tokens=value
    async def get_client_info(self):return self.info
    async def set_client_info(self,value):self.info=value

@unittest.skipUnless(os.environ.get('ARROW_TEST_KEYCLOAK')=='1','Set ARROW_TEST_KEYCLOAK=1 to run real container/OAuth tests')
class KeycloakTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='arrow-keycloak-')
        cls.work=Path(cls.temp.name)
        cls.port=port();cls.mcp_port=port()
        cls.issuer=f'http://127.0.0.1:{cls.port}/realms/arrow'
        cls.mcp=f'http://127.0.0.1:{cls.mcp_port}/mcp'
        cls.redirect=f'http://127.0.0.1:{port()}/callback'
        cls.password=secrets.token_urlsafe(32)
        realm=deploy.realm_config(cls.mcp,[cls.redirect])
        realm['sslRequired']='none'  # Loopback test container; production import requires HTTPS.
        realm['users']=[{'username':name,'enabled':True,'email':name+'@example.test','emailVerified':True,
                        'firstName':'Test','lastName':'Account','realmRoles':roles,
                        'credentials':[{'type':'password','value':cls.password,'temporary':False}]} for name,roles in (
                            ('editor',['arrow-editor']),('reader',['arrow-reader']),('outsider',[]))]
        directory=cls.work/'realm';directory.mkdir(mode=0o755)
        realm_file=directory/'arrow.json'
        realm_file.write_text(json.dumps(realm))
        realm_file.chmod(0o644)
        cls.container='arrow-keycloak-'+secrets.token_hex(5)
        command=['docker','run','--detach','--rm','--name',cls.container,'--memory','768m','--cpus','2',
            '-p',f'127.0.0.1:{cls.port}:8080','-v',str(directory)+':/opt/keycloak/data/import:ro',
            'quay.io/keycloak/keycloak:26.8.1','start-dev','--import-realm','--hostname',f'http://127.0.0.1:{cls.port}']
        subprocess.run(command,check=True,capture_output=True,text=True)
        with httpx.Client(timeout=5) as client:
            for _ in range(240):
                try:
                    if client.get(cls.issuer+'/.well-known/openid-configuration').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.5)
            else:
                logs=subprocess.check_output(['docker','logs',cls.container],text=True,stderr=subprocess.STDOUT)
                subprocess.run(['docker','stop',cls.container],capture_output=True)
                raise RuntimeError('Keycloak failed to start: '+logs[-5000:])
        environment=os.environ.copy()
        environment.update(ARROW_STORY_API_TOKEN=secrets.token_urlsafe(32),ARROW_STORY_URL='http://127.0.0.1:1',
            ARROW_MCP_PUBLIC_URL=cls.mcp,ARROW_MCP_ISSUER_URL=cls.issuer,
            ARROW_MCP_JWKS_URL=cls.issuer+'/protocol/openid-connect/certs',ARROW_MCP_KEYCLOAK_ROLES='true')
        cls.log=(cls.work/'mcp.log').open('w')
        cls.process=subprocess.Popen([sys.executable,'-m','services.story_mcp','--transport','streamable-http','--port',str(cls.mcp_port)],
            cwd=ROOT,env=environment,stdout=cls.log,stderr=subprocess.STDOUT)
        with httpx.Client() as client:
            for _ in range(100):
                try:
                    if client.get(cls.mcp.replace('/mcp','/health')).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('MCP server did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:cls.process.kill();cls.process.wait()
        cls.log.close()
        subprocess.run(['docker','stop',cls.container],capture_output=True,timeout=30)
        cls.temp.cleanup()

    async def authorize(self,url,username):
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(executable_path=os.environ.get('ARROW_TEST_CHROMIUM'),
                headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            page=await browser.new_page()
            await page.route(self.redirect+'**',lambda route:route.fulfill(status=200,body='Test OAuth callback received'))
            await page.goto(url,wait_until='domcontentloaded')
            await page.locator('[name="username"]').fill(username)
            await page.locator('[name="password"]').fill(self.password)
            await page.locator('#kc-login').click()
            await page.wait_for_function('callback => location.href.startsWith(callback) || !!document.querySelector("[name=accept]")',arg=self.redirect)
            if not page.url.startswith(self.redirect):
                await page.locator('[name="accept"]').click()
            await page.wait_for_url(self.redirect+'**')
            result=parse_qs(urlsplit(page.url).query)
            self.assertEqual(result.get('iss'),[self.issuer])
            await browser.close()
            return result

    async def test_official_sdk_discovers_keycloak_signs_in_and_binds_permissions(self):
        storage=Storage(self.redirect);callback={}
        async def redirect(url):callback.update(await self.authorize(url,'editor'))
        async def get_callback():return callback['code'][0],callback['state'][0]
        metadata=OAuthClientMetadata(redirect_uris=[AnyUrl(self.redirect)],token_endpoint_auth_method='none',scope='openid arrow:read arrow:edit arrow:apply')
        auth=OAuthClientProvider(server_url=self.mcp,client_metadata=metadata,storage=storage,
            redirect_handler=redirect,callback_handler=get_callback)
        async with httpx.AsyncClient(auth=auth,timeout=30) as client:
            async with streamable_http_client(self.mcp,http_client=client) as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    tools=await session.list_tools();self.assertEqual(len(tools.tools),6)
                    self.assertIn('securitySchemes',tools.tools[0].meta)
                    resource=await session.read_resource('arrow://story-contract');self.assertTrue(resource.contents)
        self.assertIsNotNone(storage.tokens)
        claims=jwt.decode(storage.tokens.access_token,options={'verify_signature':False})
        self.assertEqual(claims['aud'],self.mcp)
        self.assertEqual(claims['iss'],self.issuer)
        self.assertIn('arrow-editor',claims['realm_access']['roles'])
        self.assertIsNotNone(storage.tokens.refresh_token)
        async with httpx.AsyncClient() as client:
            response=await client.post(self.issuer+'/protocol/openid-connect/token',data={
                'grant_type':'refresh_token','client_id':'arrow-chatgpt','refresh_token':storage.tokens.refresh_token})
            self.assertEqual(response.status_code,200)
            self.assertNotEqual(response.json()['refresh_token'],storage.tokens.refresh_token)
            reused=await client.post(self.issuer+'/protocol/openid-connect/token',data={
                'grant_type':'refresh_token','client_id':'arrow-chatgpt','refresh_token':storage.tokens.refresh_token})
            self.assertEqual(reused.status_code,400)
            # The authorization code cannot be exchanged again.
            replay=await client.post(self.issuer+'/protocol/openid-connect/token',data={
                'grant_type':'authorization_code','client_id':'arrow-chatgpt','code':callback['code'][0],'redirect_uri':self.redirect,'code_verifier':'wrong'})
            self.assertEqual(replay.status_code,400)

    async def test_pkce_required_and_google_account_without_role_cannot_read(self):
        async with httpx.AsyncClient() as client:
            metadata=(await client.get(self.issuer+'/.well-known/openid-configuration')).json()
            self.assertIn('S256',metadata['code_challenge_methods_supported'])
            self.assertTrue(metadata['authorization_response_iss_parameter_supported'])
            request={'client_id':'arrow-chatgpt','redirect_uri':self.redirect,'response_type':'code','scope':'openid arrow:read'}
            response=await client.get(metadata['authorization_endpoint'],params=request)
            self.assertIn(response.status_code,(302,400))
            if response.is_redirect:
                self.assertIn('error=',response.headers['location'])
            else:
                self.assertIn('code_challenge',response.text)
        # Role-less local accounts model a newly created brokered Google account.
        import hashlib,base64
        verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        url=self.issuer+'/protocol/openid-connect/auth?'+urlencode(request|{'code_challenge':challenge,'code_challenge_method':'S256','state':'outsider'})
        result=await self.authorize(url,'outsider')
        async with httpx.AsyncClient() as client:
            response=await client.post(self.issuer+'/protocol/openid-connect/token',data={
                'grant_type':'authorization_code','client_id':'arrow-chatgpt','code':result['code'][0],'redirect_uri':self.redirect,'code_verifier':verifier})
            self.assertEqual(response.status_code,200)
            denied=await client.post(self.mcp,headers={'Authorization':'Bearer '+response.json()['access_token']},json={})
            self.assertEqual(denied.status_code,403)

    async def test_unregistered_redirect_and_incorrect_pkce_are_rejected(self):
        import hashlib,base64
        request={'client_id':'arrow-chatgpt','redirect_uri':'https://attacker.invalid/callback',
                 'response_type':'code','scope':'openid arrow:read','code_challenge':'x'*43,'code_challenge_method':'S256'}
        async with httpx.AsyncClient() as client:
            response=await client.get(self.issuer+'/protocol/openid-connect/auth',params=request)
            self.assertEqual(response.status_code,400)
            self.assertNotIn('location',response.headers)
        verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        request.update(redirect_uri=self.redirect,code_challenge=challenge,state='wrong-pkce')
        result=await self.authorize(self.issuer+'/protocol/openid-connect/auth?'+urlencode(request),'reader')
        async with httpx.AsyncClient() as client:
            denied=await client.post(self.issuer+'/protocol/openid-connect/token',data={
                'grant_type':'authorization_code','client_id':'arrow-chatgpt','code':result['code'][0],
                'redirect_uri':self.redirect,'code_verifier':secrets.token_urlsafe(48)})
            self.assertEqual(denied.status_code,400)
            self.assertNotIn('access_token',denied.json())

if __name__=='__main__':unittest.main()
