#!/usr/bin/env python3
"""Create an Amazon Cognito user pool configured for machine-to-machine
(client-credentials) OAuth, to use as the gateway's inbound authorizer.

AgentCore Gateway requires callers to present a valid JWT (OAuth2 bearer
token). For an autonomous agent there is no human logging in, so we use the
OAuth2 *client credentials* grant: the agent presents a client id + secret to
Cognito's token endpoint and gets back an access token, which it then sends to
the gateway as `Authorization: Bearer <token>`.

This script creates:
  * a user pool
  * a resource server that declares a custom scope (e.g. "mcp-orders/invoke")
  * a hosted-UI domain (needed for the /oauth2/token endpoint)
  * an app client with a secret, allowed to use client_credentials + the scope

It then prints the values you should paste into deploy/config.sh.

Usage:
  source deploy/config.sh
  python deploy/20_setup_cognito.py
"""

import os
import sys

import boto3
from botocore.exceptions import ClientError

REGION = os.environ["AWS_REGION"]
POOL_NAME = "mcp-orders-pool"
RESOURCE_SERVER_ID = "mcp-orders"      # identifier the scope is namespaced under
SCOPE_NAME = "invoke"                   # full scope becomes "mcp-orders/invoke"
CLIENT_NAME = "mcp-orders-agent-client"
# Domain prefixes are global within a region; add a suffix if this collides.
DOMAIN_PREFIX = f"mcp-orders-{os.environ['ACCOUNT_ID']}"

cognito = boto3.client("cognito-idp", region_name=REGION)


def main():
    pool_id = _create_user_pool()
    _create_resource_server(pool_id)
    _create_domain(pool_id)
    client_id, client_secret = _create_app_client(pool_id)

    scope = f"{RESOURCE_SERVER_ID}/{SCOPE_NAME}"
    discovery_url = (
        f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}"
        f"/.well-known/openid-configuration"
    )
    token_url = f"https://{DOMAIN_PREFIX}.auth.{REGION}.amazoncognito.com/oauth2/token"

    print("\n" + "=" * 72)
    print("Cognito is ready. Paste these into deploy/config.sh:")
    print("=" * 72)
    print(f'export COGNITO_USER_POOL_ID="{pool_id}"')
    print(f'export COGNITO_CLIENT_ID="{client_id}"')
    print(f'export COGNITO_CLIENT_SECRET="{client_secret}"')
    print(f'export COGNITO_DISCOVERY_URL="{discovery_url}"')
    print(f'export COGNITO_TOKEN_URL="{token_url}"')
    print(f'export COGNITO_SCOPE="{scope}"')
    print("=" * 72)
    print("\nNext: re-`source deploy/config.sh`, then run")
    print("      python deploy/30_setup_gateway.py")


def _create_user_pool():
    resp = cognito.create_user_pool(PoolName=POOL_NAME)
    pool_id = resp["UserPool"]["Id"]
    print(f"==> Created user pool {pool_id}")
    return pool_id


def _create_resource_server(pool_id):
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier=RESOURCE_SERVER_ID,
        Name="MCP Orders Resource Server",
        Scopes=[{"ScopeName": SCOPE_NAME, "ScopeDescription": "Invoke MCP tools"}],
    )
    print(f"==> Created resource server '{RESOURCE_SERVER_ID}' with scope "
          f"'{RESOURCE_SERVER_ID}/{SCOPE_NAME}'")


def _create_domain(pool_id):
    try:
        cognito.create_user_pool_domain(Domain=DOMAIN_PREFIX, UserPoolId=pool_id)
        print(f"==> Created hosted domain '{DOMAIN_PREFIX}'")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "InvalidParameterException":
            print(f"!!! Domain prefix '{DOMAIN_PREFIX}' may be taken. "
                  f"Edit DOMAIN_PREFIX in this script and retry.")
            raise
        raise


def _create_app_client(pool_id):
    resp = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=CLIENT_NAME,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[f"{RESOURCE_SERVER_ID}/{SCOPE_NAME}"],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
    )
    client = resp["UserPoolClient"]
    print(f"==> Created app client {client['ClientId']}")
    return client["ClientId"], client["ClientSecret"]


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        sys.exit(1)
