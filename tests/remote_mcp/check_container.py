"""Smoke-test the built non-root image and sidecar layout without public deployment."""
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request

from services.story_service.client import Client, load_bundle

ROOT=Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        return listener.getsockname()[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='arrow-remote-mcp:test')
    args=parser.parse_args()
    prefix='arrow-image-test-'+secrets.token_hex(5)
    api_port,mcp_port=port(),port()
    api=f'http://127.0.0.1:{api_port}'
    mcp=f'http://127.0.0.1:{mcp_port}'
    environment=os.environ|{'ARROW_STORY_API_TOKEN':secrets.token_urlsafe(48),
        'ARROW_STORY_URL':'http://127.0.0.1:8765','ARROW_MCP_PUBLIC_URL':mcp+'/mcp',
        'ARROW_MCP_ISSUER_URL':'https://auth.example.test/realms/arrow',
        'ARROW_MCP_JWKS_URL':'https://auth.example.test/realms/arrow/certs','ARROW_MCP_KEYCLOAK_ROLES':'true'}

    def docker(*arguments):
        return subprocess.run(['docker',*arguments],env=environment,capture_output=True,text=True,check=True).stdout.strip()

    def wait(url):
        for _ in range(120):
            try:
                with urllib.request.urlopen(url,timeout=2) as response:
                    if response.status==200:return
            except OSError:pass
            time.sleep(.25)
        raise RuntimeError('Container failed health check')

    try:
        assert docker('image','inspect',args.image,'--format','{{.Config.User}}')=='arrow'
        docker('volume','create',prefix)
        docker('run','-d','--name',prefix,'--env','ARROW_STORY_API_TOKEN',
            '-p',f'127.0.0.1:{api_port}:8765','-p',f'127.0.0.1:{mcp_port}:8766',
            '--mount',f'type=volume,src={prefix},dst=/data',args.image)
        wait(api+'/health')
        client=Client(api,environment['ARROW_STORY_API_TOKEN'])
        bundle=load_bundle(ROOT/'examples/dialogic_bridge/story/guild_story.json')
        bundle['package']['id']='container_smoke'
        created=client.request('POST','/v1/stories',{'bundle':bundle,'message':'Non-canon image smoke test'})
        assert created['revision']==1
        proposal=client.request('POST','/v1/stories/container_smoke/proposals',{'base_revision':1,
            'message':'Container-native validation','operations':[{'op':'update_node','document':'briefing','node':'2',
            'data':{'content':'Verified inside the non-root container.'}}]})
        assert proposal['status']=='ready'
        result=client.request('POST','/v1/stories/container_smoke/proposals/'+proposal['id']+'/apply',{})
        assert result['revision']==2
        docker('restart',prefix)
        wait(api+'/health')
        assert client.request('GET','/v1/stories/container_smoke')['revision']==2
        remote_env=[]
        for name in ('ARROW_STORY_API_TOKEN','ARROW_STORY_URL','ARROW_MCP_PUBLIC_URL',
                     'ARROW_MCP_ISSUER_URL','ARROW_MCP_JWKS_URL','ARROW_MCP_KEYCLOAK_ROLES'):
            remote_env.extend(['--env',name])
        docker('run','-d','--name',prefix+'-mcp','--network','container:'+prefix,*remote_env,
            args.image,'python','-m','services.story_mcp','--transport','streamable-http','--host','0.0.0.0')
        wait(mcp+'/health')
        with urllib.request.urlopen(mcp+'/.well-known/oauth-protected-resource/mcp') as response:
            assert response.status==200
        try:
            urllib.request.urlopen(urllib.request.Request(mcp+'/mcp',data=b'{}',headers={'Content-Type':'application/json'}))
        except urllib.error.HTTPError as error:
            assert error.code==401
            assert 'resource_metadata=' in error.headers['www-authenticate']
            error.close()
        else:raise AssertionError('Remote MCP accepted an unauthenticated request')
        print('PASS: non-root image, native validation, revision commit/persistence, sidecar discovery and HTTP authentication')
    except Exception:
        for name in (prefix,prefix+'-mcp'):
            logs=subprocess.run(['docker','logs','--tail','40',name],capture_output=True,text=True)
            print(logs.stdout,logs.stderr)
        raise
    finally:
        for name in (prefix+'-mcp',prefix):
            subprocess.run(['docker','rm','--force',name],capture_output=True)
        subprocess.run(['docker','volume','rm',prefix],capture_output=True)


if __name__=='__main__':main()
