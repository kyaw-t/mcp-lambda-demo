#!/usr/bin/env python3
"""Create the AgentCore Gateway IAM role, the gateway itself, and a Lambda
target that exposes the tools defined in schemas/tool_schema.json.

This is the step that turns a plain Lambda function into an MCP server.

Pipeline:
  1. Create the gateway's execution role (trusted by bedrock-agentcore, allowed
     to invoke the tool Lambda).                          -> "outbound auth"
  2. Create the gateway with a CUSTOM_JWT inbound authorizer pointing at the
     Cognito pool from 20_setup_cognito.py.               -> "inbound auth"
  3. Create a Lambda target on the gateway, attaching the tool schema. The
     target name becomes the tool-name prefix (TARGET_NAME___tool).

Usage:
  source deploy/config.sh          # must include the Cognito values
  python deploy/30_setup_gateway.py
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = os.environ["AWS_REGION"]
ACCOUNT_ID = os.environ["ACCOUNT_ID"]
LAMBDA_FUNCTION_NAME = os.environ["LAMBDA_FUNCTION_NAME"]
GATEWAY_ROLE_NAME = os.environ["GATEWAY_ROLE_NAME"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
TARGET_NAME = os.environ["TARGET_NAME"]

# Inbound-auth values produced by 20_setup_cognito.py
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
COGNITO_DISCOVERY_URL = os.environ["COGNITO_DISCOVERY_URL"]

LAMBDA_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{LAMBDA_FUNCTION_NAME}"
REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "tool_schema.json"

iam = boto3.client("iam")
# The control-plane client. Region must be one where AgentCore is available.
agentcore = boto3.client("bedrock-agentcore-control", region_name=REGION)


def main():
    role_arn = ensure_gateway_role()
    # IAM is eventually consistent; give the new role a moment before the
    # gateway service tries to assume it.
    print("    waiting ~10s for the gateway role to propagate ...")
    time.sleep(10)

    gateway_id, gateway_url = create_gateway(role_arn)
    create_lambda_target(gateway_id)

    print("\n" + "=" * 72)
    print("Gateway is ready. Paste these into deploy/config.sh:")
    print("=" * 72)
    print(f'export GATEWAY_ID="{gateway_id}"')
    print(f'export GATEWAY_MCP_URL="{gateway_url}"')
    print("=" * 72)
    print("\nNext: re-`source deploy/config.sh`, then run")
    print("      python client/test_client.py")


def ensure_gateway_role():
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{GATEWAY_ROLE_NAME}"
    trust = json.loads((REPO_ROOT / "deploy/iam/gateway-trust-policy.json").read_text())
    perms = json.loads(
        (REPO_ROOT / "deploy/iam/gateway-permissions-policy.json")
        .read_text()
        .replace("REGION", REGION)
        .replace("ACCOUNT_ID", ACCOUNT_ID)
        .replace("mcp-orders-tool", LAMBDA_FUNCTION_NAME)
    )

    try:
        iam.create_role(
            RoleName=GATEWAY_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Execution role AgentCore Gateway assumes to invoke tool Lambdas.",
        )
        print(f"==> Created gateway role {GATEWAY_ROLE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"==> Gateway role {GATEWAY_ROLE_NAME} already exists")
        else:
            raise

    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName="invoke-tool-lambda",
        PolicyDocument=json.dumps(perms),
    )
    print("    attached inline policy 'invoke-tool-lambda'")
    return role_arn


def create_gateway(role_arn):
    authorizer_config = {
        "customJWTAuthorizer": {
            # allowedClients validates the token's client_id claim. For the
            # client-credentials grant there is no `aud`, so we match on the
            # app client id rather than allowedAudience.
            "allowedClients": [COGNITO_CLIENT_ID],
            "discoveryUrl": COGNITO_DISCOVERY_URL,
        }
    }

    resp = agentcore.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration=authorizer_config,
        description="Demo gateway exposing an order-management Lambda as MCP tools.",
    )
    gateway_id = resp["gatewayId"]
    # The response key has varied across API versions; accept either.
    gateway_url = resp.get("gatewayUrl") or resp.get("gatewayEndpoint") or ""
    print(f"==> Created gateway {gateway_id}")
    print(f"    MCP endpoint: {gateway_url}")

    _wait_for_gateway_ready(gateway_id)
    return gateway_id, gateway_url


def create_lambda_target(gateway_id):
    tool_schema = json.loads(SCHEMA_PATH.read_text())
    target_config = {
        "mcp": {
            "lambda": {
                "lambdaArn": LAMBDA_ARN,
                "toolSchema": {"inlinePayload": tool_schema},
            }
        }
    }
    credential_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

    resp = agentcore.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=TARGET_NAME,
        description="Order-management Lambda target.",
        targetConfiguration=target_config,
        credentialProviderConfigurations=credential_config,
    )
    target_id = resp["targetId"]
    print(f"==> Created target {target_id} (name '{TARGET_NAME}')")
    print(f"    tools will be visible as {TARGET_NAME}___<tool_name>")


def _wait_for_gateway_ready(gateway_id, attempts=30, delay=5):
    for _ in range(attempts):
        status = agentcore.get_gateway(gatewayIdentifier=gateway_id)["status"]
        if status in ("READY", "AVAILABLE", "ACTIVE"):
            print(f"    gateway status: {status}")
            return
        if status in ("FAILED", "CREATE_FAILED"):
            raise RuntimeError(f"Gateway entered status {status}")
        time.sleep(delay)
    print("    (proceeding; gateway not yet reported READY)")


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        sys.exit(1)
