#!/usr/bin/env python3
"""End-to-end smoke test: authenticate to Cognito, connect to the gateway's
MCP endpoint, list the tools, and call a couple of them.

This proves the full chain works:
    Cognito token  ->  Gateway (inbound auth)  ->  Lambda (tool code)  ->  back

Usage:
  source deploy/config.sh          # needs the Cognito + Gateway values
  pip install -r client/requirements.txt
  python client/test_client.py
"""

import asyncio
import os
import sys

import requests
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

TOKEN_URL = os.environ["COGNITO_TOKEN_URL"]
CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
CLIENT_SECRET = os.environ["COGNITO_CLIENT_SECRET"]
SCOPE = os.environ["COGNITO_SCOPE"]
GATEWAY_MCP_URL = os.environ["GATEWAY_MCP_URL"]


def get_access_token():
    """OAuth2 client-credentials grant against the Cognito token endpoint."""
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "scope": SCOPE},
        auth=(CLIENT_ID, CLIENT_SECRET),  # HTTP Basic per RFC 6749
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def run():
    token = get_access_token()
    print("Obtained access token from Cognito.\n")

    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(GATEWAY_MCP_URL, headers=headers) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # ---- tools/list ------------------------------------------------
            tools = (await session.list_tools()).tools
            print(f"Gateway advertises {len(tools)} tools:")
            for t in tools:
                print(f"  - {t.name}: {t.description}")
            print()

            # Tool names are prefixed with the target name, e.g.
            # "OrderTools___get_order". Discover the prefix from the list so
            # this test does not hard-code it.
            prefix = tools[0].name.split("___")[0]

            # ---- tools/call: get_order ------------------------------------
            print(f"Calling {prefix}___get_order(order_id='O-5001') ...")
            result = await session.call_tool(
                f"{prefix}___get_order", {"order_id": "O-5001"}
            )
            _print_result(result)

            # ---- tools/call: search_products ------------------------------
            print(f"\nCalling {prefix}___search_products(query='headphones') ...")
            result = await session.call_tool(
                f"{prefix}___search_products", {"query": "headphones"}
            )
            _print_result(result)


def _print_result(result):
    for block in result.content:
        # Tool results come back as text content blocks (JSON-encoded).
        text = getattr(block, "text", None)
        if text is not None:
            print("  ->", text)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyError as exc:
        print(f"Missing env var {exc}. Did you `source deploy/config.sh`?",
              file=sys.stderr)
        sys.exit(1)
