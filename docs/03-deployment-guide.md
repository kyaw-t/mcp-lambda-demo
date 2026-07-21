# 3. Deployment guide

Step-by-step from an empty account to a working MCP endpoint. Every step lists
the automated script **and** the equivalent raw AWS CLI / boto3 call, so you can
see exactly what is happening under the hood.

## Prerequisites

- **AWS CLI v2**, configured with credentials for the target account
  (`aws configure` or an SSO profile).
- **Python 3.10+** locally with `boto3` and `requests`:
  ```bash
  pip install boto3 requests
  pip install -r client/requirements.txt   # adds the `mcp` client SDK
  ```
- **A region where AgentCore is available** (e.g. `us-east-1`, `us-west-2`).
  Check the AgentCore docs for the current region list.
- IAM permissions to create roles/policies, Lambda functions, a Cognito user
  pool, and `bedrock-agentcore` gateways/targets. Broadly: `iam:*` on the role
  names below, `lambda:*`, `cognito-idp:*`, and `bedrock-agentcore:*`.

## Overview of the pipeline

```
10_deploy_lambda.sh   ->  Lambda function + its execution role
20_setup_cognito.py   ->  Cognito user pool (inbound auth)
30_setup_gateway.py   ->  Gateway IAM role + Gateway + Lambda target
client/test_client.py ->  authenticate + list + call tools
```

Each script prints the values you paste into `deploy/config.sh` for the next
step.

---

## Step 0 — Configure

```bash
cp deploy/config.example.sh deploy/config.sh
# Edit AWS_REGION and ACCOUNT_ID (get the account id with the command below):
aws sts get-caller-identity --query Account --output text
source deploy/config.sh
```

`config.sh` is git-ignored (it will hold a client secret). Leave the Cognito and
Gateway sections blank for now — later steps fill them in.

---

## Step 1 — Deploy the Lambda

```bash
./deploy/10_deploy_lambda.sh
```

This creates the execution role, packages `src/lambda/` into a zip, and
creates (or updates) the function `mcp-orders-tool`. Re-run it any time you
change the tool code — it updates in place.

<details>
<summary>What it does with raw AWS CLI</summary>

```bash
# 1. Execution role (standard Lambda trust + basic logging policy)
aws iam create-role --role-name mcp-orders-tool-lambda-role \
  --assume-role-policy-document file://deploy/iam/lambda-trust-policy.json
aws iam attach-role-policy --role-name mcp-orders-tool-lambda-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# 2. Package (no third-party deps, so just zip the source)
cd src/lambda && zip -r /tmp/function.zip . && cd -

# 3. Create the function
aws lambda create-function \
  --function-name mcp-orders-tool \
  --runtime python3.12 \
  --role arn:aws:iam::<ACCOUNT_ID>:role/mcp-orders-tool-lambda-role \
  --handler handler.lambda_handler \
  --timeout 30 --memory-size 256 \
  --zip-file fileb:///tmp/function.zip
```
</details>

**Note on the handler string:** `handler.lambda_handler` means "the
`lambda_handler` function in `handler.py`." If you rename either, update the
`--handler` value.

### (Optional) sanity-check the function directly

You can invoke the Lambda without a gateway to confirm the code works. Because
there is no gateway, you must supply the tool name in the client context
yourself:

```bash
aws lambda invoke --function-name mcp-orders-tool \
  --cli-binary-format raw-in-base64-out \
  --payload '{"order_id":"O-5001"}' \
  --client-context "$(printf '{"custom":{"bedrockAgentCoreToolName":"OrderTools___get_order"}}' | base64)" \
  /dev/stdout
```

You should see the JSON for order `O-5001`.

---

## Step 2 — Set up Cognito (inbound auth)

```bash
python deploy/20_setup_cognito.py
```

This creates a user pool, a resource server with the scope
`mcp-orders/invoke`, a hosted domain (for the token endpoint), and an app
client that uses the **client-credentials** OAuth flow (machine-to-machine — no
human login). It prints six `export` lines.

**Paste those lines into `deploy/config.sh`, then re-source it:**

```bash
$EDITOR deploy/config.sh      # paste the COGNITO_* exports
source deploy/config.sh
```

Why client-credentials? An autonomous agent isn't a user typing a password. In
this flow the agent authenticates *as itself* with a client id + secret and
receives a short-lived access token (a JWT). The gateway validates that JWT.

<details>
<summary>How the gateway will validate these tokens</summary>

The gateway is configured (in step 3) with a `customJWTAuthorizer` pointing at:

- **`discoveryUrl`** — `https://cognito-idp.<region>.amazonaws.com/<poolId>/.well-known/openid-configuration`.
  The gateway fetches this to learn Cognito's public keys (JWKS) and validates
  every token's signature and expiry against them.
- **`allowedClients`** — the app client id. The gateway checks the token's
  `client_id` claim is in this list. (We match on `client_id` rather than
  `allowedAudience` because the client-credentials grant does not set an `aud`
  claim.)
</details>

---

## Step 3 — Create the gateway and target

```bash
python deploy/30_setup_gateway.py
```

This does three things:

1. **Creates the gateway IAM role** (`mcp-orders-gateway-role`): trusted by
   `bedrock-agentcore.amazonaws.com`, with an inline policy granting
   `lambda:InvokeFunction` on your tool Lambda. This is **outbound auth** — how
   the gateway is allowed to call your function.
2. **Creates the gateway** with `protocolType=MCP` and the `CUSTOM_JWT` inbound
   authorizer pointing at your Cognito pool.
3. **Creates a Lambda target** named `OrderTools`, attaching
   `schemas/tool_schema.json` as the inline tool schema, with
   `credentialProviderType=GATEWAY_IAM_ROLE` (use the gateway's role to invoke
   the Lambda).

It prints `GATEWAY_ID` and `GATEWAY_MCP_URL`. **Paste them into `config.sh` and
re-source.**

<details>
<summary>The core boto3 calls, unwrapped</summary>

```python
import boto3
agentcore = boto3.client("bedrock-agentcore-control", region_name=REGION)

# --- the gateway (inbound auth) ---
gw = agentcore.create_gateway(
    name="OrdersDemoGateway",
    roleArn="arn:aws:iam::<ACCOUNT_ID>:role/mcp-orders-gateway-role",
    protocolType="MCP",
    authorizerType="CUSTOM_JWT",
    authorizerConfiguration={
        "customJWTAuthorizer": {
            "allowedClients": ["<cognito app client id>"],
            "discoveryUrl": "https://cognito-idp.<region>.amazonaws.com/<poolId>/.well-known/openid-configuration",
        }
    },
)
gateway_id = gw["gatewayId"]
mcp_url    = gw["gatewayUrl"]          # the endpoint agents connect to

# --- the Lambda target (schema + outbound auth) ---
agentcore.create_gateway_target(
    gatewayIdentifier=gateway_id,
    name="OrderTools",                 # becomes the tool-name prefix
    targetConfiguration={
        "mcp": {
            "lambda": {
                "lambdaArn": "arn:aws:lambda:<region>:<acct>:function:mcp-orders-tool",
                "toolSchema": {"inlinePayload": [ ...tool defs... ]},
            }
        }
    },
    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
)
```
</details>

### The two IAM roles, side by side

Do not confuse these — they are different roles trusted by different services:

| Role | Trusted by | Purpose | Key permission |
|---|---|---|---|
| `mcp-orders-tool-lambda-role` | `lambda.amazonaws.com` | The function's own execution role | write logs (`AWSLambdaBasicExecutionRole`) |
| `mcp-orders-gateway-role` | `bedrock-agentcore.amazonaws.com` | The gateway assumes it to call the Lambda | `lambda:InvokeFunction` on the tool Lambda |

> **Alternative to the identity policy:** instead of granting
> `lambda:InvokeFunction` on the gateway role, you can add a **resource-based
> policy** on the Lambda allowing the gateway role (or the AgentCore service) to
> invoke it (`aws lambda add-permission`). Either approach works; this repo uses
> the identity-based policy because it keeps all the gateway's permissions in
> one place.

### A note on timing

New IAM roles are eventually consistent. Both scripts `sleep` ~10s after
creating a role before the next service tries to assume it. If you script this
yourself and see `AccessDenied` / "cannot assume role" right after creation,
wait and retry — it is almost always propagation lag, not a real permission
problem.

---

## Step 4 — Test it end to end

```bash
source deploy/config.sh          # now has every value filled in
python client/test_client.py
```

Expected output: an access token is fetched, `tools/list` shows all six tools
(prefixed `OrderTools___...`), and two tool calls return live JSON from the
Lambda. See [4. Testing & troubleshooting](04-testing-and-troubleshooting.md)
for what each failure mode means.

---

## Updating things later

| You changed... | Re-run |
|---|---|
| Tool code (`src/lambda/`) | `./deploy/10_deploy_lambda.sh` (updates code in place) |
| The tool schema (`schemas/tool_schema.json`) | `agentcore.update_gateway_target(...)`, or delete + recreate the target |
| Inbound auth (Cognito) | update the authorizer via `update_gateway`, or recreate |

To change the tool schema on an existing target, call `update_gateway_target`
with the same `gatewayIdentifier` and `targetId` and the new
`targetConfiguration`. Adding a brand-new tool is two edits that must stay in
sync: add it to `schemas/tool_schema.json` **and** to `TOOL_REGISTRY` in
`src/lambda/tools.py` (redeploy the Lambda), then update the target.

---

## Tearing it all down

```bash
source deploy/config.sh
# 1. Gateway target(s), then the gateway
aws bedrock-agentcore-control delete-gateway-target \
  --gateway-identifier "$GATEWAY_ID" --target-id <targetId> --region "$AWS_REGION"
aws bedrock-agentcore-control delete-gateway \
  --gateway-identifier "$GATEWAY_ID" --region "$AWS_REGION"
# 2. Lambda + roles
aws lambda delete-function --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION"
aws iam delete-role-policy --role-name "$GATEWAY_ROLE_NAME" --policy-name invoke-tool-lambda
aws iam delete-role --role-name "$GATEWAY_ROLE_NAME"
aws iam detach-role-policy --role-name "$LAMBDA_ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name "$LAMBDA_ROLE_NAME"
# 3. Cognito
aws cognito-idp delete-user-pool-domain --domain <DOMAIN_PREFIX> --user-pool-id "$COGNITO_USER_POOL_ID"
aws cognito-idp delete-user-pool --user-pool-id "$COGNITO_USER_POOL_ID"
```

## Sources

- [AWS Lambda function targets — AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html)
- [Set up inbound authorization for your gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)
- [Use an AgentCore gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using.html)
- [Cognito token endpoint (client credentials)](https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html)
