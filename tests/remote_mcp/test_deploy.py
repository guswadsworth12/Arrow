"""Validate private deployment generation and the production Compose contract."""
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]


class DeploymentTests(unittest.TestCase):
    def prepare(self,directory,*extra):
        return subprocess.run([sys.executable,str(ROOT/'deploy/remote-mcp/prepare.py'),
            '--workspace-domain','Workspace.Example.com','--auth-domain','auth.example.com',
            '--redirect-uri','https://chatgpt.com/connector/oauth/exact-test-callback',
            '--directory',str(directory),*extra],capture_output=True,text=True,cwd=ROOT)

    def test_generation_is_private_no_overwrite_and_requires_valid_domains(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)
            result=self.prepare(target);self.assertEqual(result.returncode,0,result.stderr)
            env=target/'.env';before=env.read_bytes()
            self.assertEqual(stat.S_IMODE(env.stat().st_mode),0o600)
            values=dict(line.split('=',1) for line in before.decode().splitlines())
            secrets=[values[key] for key in ('ARROW_STORY_API_TOKEN','ARROW_AUTH_DB_PASSWORD','KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD')]
            self.assertEqual(len(set(secrets)),3)
            self.assertTrue(all(len(value)>=40 and value not in result.stdout for value in secrets))
            self.assertNotEqual(self.prepare(target).returncode,0)
            self.assertEqual(env.read_bytes(),before)
            realm=json.loads((target/'generated/arrow-realm.json').read_text())
            self.assertEqual(stat.S_IMODE((target/'generated/arrow-realm.json').stat().st_mode),0o644)
            self.assertEqual(realm['sslRequired'],'external')
            self.assertFalse(realm['registrationAllowed'])
            self.assertFalse(realm['identityProviders'][0]['enabled'])
            self.assertNotIn('users',realm)
            client=realm['clients'][0]
            self.assertTrue(client['publicClient']);self.assertTrue(client['consentRequired'])
            self.assertFalse(client['directAccessGrantsEnabled']);self.assertFalse(client['implicitFlowEnabled'])
            self.assertEqual(client['attributes']['pkce.code.challenge.method'],'S256')
            self.assertEqual(client['redirectUris'],['https://chatgpt.com/connector/oauth/exact-test-callback'])
            audience=next(mapper for mapper in client['protocolMappers'] if mapper['protocolMapper']=='oidc-audience-mapper')
            self.assertEqual(audience['config']['included.custom.audience'],'https://workspace.example.com/mcp')
            if shutil.which('docker'):
                checked=subprocess.run(['docker','compose','--env-file',str(env),'-f',str(ROOT/'deploy/remote-mcp/compose.yml'),'config','--quiet'],capture_output=True,text=True)
                self.assertEqual(checked.returncode,0,checked.stderr)
        for domain in ('a..example.com','bad.-name.com','https://example.com','*.example.com','127.0.0.1'):
            with tempfile.TemporaryDirectory() as directory:
                self.assertNotEqual(self.prepare(directory,'--workspace-domain',domain).returncode,0)
                self.assertFalse((Path(directory)/'.env').exists())

if __name__=='__main__':unittest.main()
