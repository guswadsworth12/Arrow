#!/usr/bin/env python3
"""Prepare a private deployment env and public Keycloak realm import; no live deployment."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.story_mcp.remote import http_url


def realm_config(resource: str, redirects: list[str]) -> dict:
    resource = http_url(resource, 'MCP resource')
    if not redirects:
        raise ValueError('Provide the exact OAuth redirect URI shown by the MCP client')
    for value in redirects:
        http_url(value, 'OAuth redirect')
    return {
        'realm': 'arrow', 'enabled': True, 'sslRequired': 'external', 'registrationAllowed': False,
        'bruteForceProtected': True, 'accessTokenLifespan': 300, 'accessCodeLifespan': 60,
        'revokeRefreshToken': True, 'refreshTokenMaxReuse': 0,
        'ssoSessionIdleTimeout': 1800, 'ssoSessionMaxLifespan': 36000,
        'roles': {'realm': [{'name': name} for name in ('arrow-reader','arrow-editor','arrow-committer')]},
        'clientScopes': [{'name': scope, 'protocol': 'openid-connect', 'attributes': {
            'include.in.token.scope': 'true', 'display.on.consent.screen': 'true',
            'consent.screen.text': label}} for scope,label in (
                ('arrow:read','Read this Arrow workspace'), ('arrow:edit','Propose Arrow story edits'),
                ('arrow:apply','Commit reviewed Arrow proposals'))],
        'clients': [{
            'clientId': 'arrow-chatgpt', 'name': 'Arrow story workspace', 'enabled': True,
            'protocol': 'openid-connect', 'publicClient': True, 'standardFlowEnabled': True,
            'implicitFlowEnabled': False, 'directAccessGrantsEnabled': False, 'serviceAccountsEnabled': False,
            'consentRequired': True, 'fullScopeAllowed': True,
            'redirectUris': redirects, 'webOrigins': [],
            'defaultClientScopes': ['arrow:read'], 'optionalClientScopes': ['arrow:edit','arrow:apply'],
            'attributes': {'pkce.code.challenge.method': 'S256', 'exclude.issuer.from.auth.response': 'false'},
            'protocolMappers': [
                {'name': 'Subject identifier', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-sub-mapper',
                 'config': {'access.token.claim': 'true', 'introspection.token.claim': 'true'}},
                {'name': 'Arrow resource audience', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-audience-mapper',
                 'config': {'included.custom.audience': resource, 'access.token.claim': 'true', 'id.token.claim': 'false'}},
                {'name': 'Arrow account roles', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-usermodel-realm-role-mapper',
                 'config': {'claim.name': 'realm_access.roles', 'jsonType.label': 'String', 'multivalued': 'true',
                            'access.token.claim': 'true', 'id.token.claim': 'false', 'userinfo.token.claim': 'false'}},
            ]}],
        # Enable only after supplying the Google OAuth client in Keycloak's admin UI.
        'identityProviders': [{'alias': 'google', 'providerId': 'google', 'enabled': False,
                              'trustEmail': False, 'storeToken': False, 'firstBrokerLoginFlowAlias': 'first broker login',
                              'config': {'clientId': '', 'clientSecret': '', 'defaultScope': 'openid email profile'}}],
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-domain',required=True)
    parser.add_argument('--auth-domain',required=True)
    parser.add_argument('--redirect-uri',action='append',required=True)
    parser.add_argument('--directory',type=Path,default=Path(__file__).parent)
    args=parser.parse_args()
    args.workspace_domain=args.workspace_domain.lower()
    args.auth_domain=args.auth_domain.lower()
    for value in (args.workspace_domain,args.auth_domain):
        labels=value.split('.')
        if (len(value)>253 or len(labels)<2 or all(label.isdigit() for label in labels)
                or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in labels)):
            parser.error('Use public DNS hostnames without scheme, port, paths or wildcards')
    if args.workspace_domain.lower()==args.auth_domain.lower():
        parser.error('Use separate workspace and authentication hostnames')
    resource='https://'+args.workspace_domain+'/mcp'
    realm=realm_config(resource,args.redirect_uri)
    target=args.directory.resolve(); target.mkdir(parents=True,exist_ok=True)
    env=target/'.env'; generated=target/'generated'
    if env.exists() or generated.exists():
        parser.error('Existing environment/realm files are preserved; choose a new directory or edit them deliberately')
    values={
        'WORKSPACE_DOMAIN': args.workspace_domain, 'AUTH_DOMAIN': args.auth_domain,
        'ARROW_STORY_API_TOKEN': secrets.token_urlsafe(48), 'ARROW_AUTH_DB_PASSWORD': secrets.token_urlsafe(48),
        'KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME': 'arrow-admin', 'KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD': secrets.token_urlsafe(32),
        'ARROW_MCP_ALLOW_APPLY': 'false', 'ARROW_STORY_WEB_ROOT': '',
    }
    descriptor=os.open(env,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:
        stream.write(''.join(key+'='+value+'\n' for key,value in values.items()))
    generated.mkdir(mode=0o755)
    realm_file=generated/'arrow-realm.json'
    realm_file.write_text(json.dumps(realm,indent=2)+'\n')
    realm_file.chmod(0o644)  # Public configuration must be readable by Keycloak's container UID.
    print('Prepared private .env and Keycloak realm in',target)
    print('No server started. Google login stays disabled until its OAuth client is configured in Keycloak.')

if __name__=='__main__':main()
