# 4. Testing & troubleshooting

## Ways to test, from lowest to highest fidelity

### 1. Local unit test of the handler (no AWS)

The handler is plain Python. Simulate a gateway invocation by faking the client
context:

```python
import sys; sys.path.insert(0, "src/lambda")
import handler

class Ctx:
    class client_context:
        custom = {"bedrockAgentCoreToolName": "OrderTools___get_order"}

print(handler.lambda_handler({"order_id": "O-5001"}, Ctx()))
# -> {'order_id': 'O-5001', 'status': 'SHIPPED', ...}
```

This catches routing bugs, prefix-stripping bugs, and tool-logic bugs in
milliseconds. Run it before every deploy.

### 2. Invoke the deployed Lambda directly (AWS, no gateway)

Confirms the packaged function runs in the real runtime. You must supply the
tool name yourself via `--client-context` (base64-encoded JSON):

```bash
aws lambda invoke --function-name mcp-orders-tool \
  --cli-binary-format raw-in-base64-out \
  --payload '{"customer_id":"C-900"}' \
  --client-context "$(printf '{"custom":{"bedrockAgentCoreToolName":"OrderTools___list_orders"}}' | base64)" \
  /dev/stdout
```

### 3. Full MCP call through the gateway

`python client/test_client.py` — exercises inbound auth, the gateway, outbound
auth, and the Lambda together. This is the real thing an agent does.

### 4. `tools/list` with a raw curl (quick gateway liveness check)

Once you have a token, you can hit the MCP endpoint directly:

```bash
TOKEN=$(curl -s -X POST "$COGNITO_TOKEN_URL" \
  -u "$COGNITO_CLIENT_ID:$COGNITO_CLIENT_SECRET" \
  -d "grant_type=client_credentials&scope=$COGNITO_SCOPE" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s "$GATEWAY_MCP_URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## Troubleshooting matrix

### Auth / token problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` from the gateway | No token, expired token, or bad signature | Re-fetch the token; confirm `discoveryUrl` on the gateway matches the pool that minted the token. |
| `403 Forbidden`, `insufficient_scope` | Token valid but missing the required scope | Ensure the app client's `AllowedOAuthScopes` includes the scope and you request it in the token call. |
| Gateway rejects a valid-looking token | `client_id` claim not in `allowedClients` | The token's `client_id` must be in the gateway's `allowedClients`. Recreate/update the authorizer with the right app client id. |
| Token endpoint returns `invalid_client` | Wrong client id/secret, or client secret not sent as HTTP Basic | Use HTTP Basic auth (`-u id:secret`), not form fields. |
| Token endpoint returns `invalid_scope` | Scope string wrong | It must be `<resourceServerId>/<scopeName>`, e.g. `mcp-orders/invoke`. |

### Tool routing / execution problems

| Symptom | Likely cause | Fix |
|---|---|---|
| Tool result is `{"error": "Unknown tool '<x>'"}` | Schema has a tool your Lambda's `TOOL_REGISTRY` doesn't | Add the handler, or remove the tool from the schema. Names must match after prefix-stripping. |
| Handler seems to receive the wrong tool | Not stripping the `<target>___` prefix | Split on `___` and take the part after it (see `_resolve_tool_name`). |
| `KeyError: 'bedrockAgentCoreToolName'` | Invoked outside a gateway with no client context | Only the gateway injects it; supply it yourself when testing directly (see above). The handler already guards this and returns a clear error. |
| Every call returns an internal error | Bug in tool code; check the stack trace | Open the Lambda's CloudWatch log group `/aws/lambda/mcp-orders-tool`. |
| Model gets an argument the schema didn't declare | Schema drift | Regenerate the target's schema from `schemas/tool_schema.json`. |

### Gateway / IAM / plumbing problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `create_gateway` fails: cannot assume role | Gateway role trust policy wrong, or IAM propagation lag | Trust principal must be `bedrock-agentcore.amazonaws.com`; wait ~10s after creating the role and retry. |
| Tool call fails: gateway can't invoke Lambda (`AccessDeniedException`) | Gateway role lacks `lambda:InvokeFunction` on the function | Check the inline `invoke-tool-lambda` policy resource ARN matches the real function ARN (region + account + name). |
| `tools/list` is empty | Target not created, or still initializing | Confirm the target exists (`list_gateway_targets`); wait for the gateway/target to become READY. |
| `ResourceNotFoundException` / unknown service `bedrock-agentcore-control` | Region without AgentCore, or an old boto3 | Use a supported region; `pip install -U boto3`. |
| Client hangs connecting to the MCP URL | Wrong URL, or missing `Accept: text/event-stream` | Use the exact `gatewayUrl` from `create_gateway`; the MCP SDK sets the right headers. |

---

## Where to look when something is off

1. **CloudWatch Logs** for the Lambda: `/aws/lambda/mcp-orders-tool`. The
   handler logs every invocation with the resolved tool name and args, plus
   full stack traces on unexpected errors.
2. **CloudTrail** for `bedrock-agentcore` events — gateway/target creation and
   invocation, and for JWT inbound auth, the token `sub` claim (avoid PII in
   `sub` for this reason).
3. **The token itself** — decode the JWT at the payload level (base64) to verify
   `client_id`, `scope`, `exp`, and `iss` are what the gateway expects. Never
   paste production tokens into third-party web decoders.

---

## Good practices for real tools

- **Validate inputs in the Lambda.** The schema guides the model but is not a
  hard security boundary — defend against missing/malformed args (this demo
  does, via `_require_str` and `ToolInputError`).
- **Set a sensible timeout.** The function timeout must exceed your slowest
  tool. The demo uses 30s.
- **Return concise, structured results.** The output goes into an LLM's context
  window. Return what the agent needs, not giant blobs.
- **Write great `description`s.** Tool selection quality is mostly driven by the
  `name` + `description` the model reads. Be specific about when to use each
  tool.
- **Least-privilege IAM.** Scope the gateway role's `lambda:InvokeFunction` to
  the specific function ARN (as this repo does), not `*`.
- **Idempotency for writes.** `create_order`-style tools may be retried by an
  agent; design them so a retry doesn't double-charge.

Back to [README](../README.md) · [Architecture](architecture.md)
