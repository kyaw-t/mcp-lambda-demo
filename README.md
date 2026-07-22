# mcp-lambda-demo

A complete, working example of exposing an **AWS Lambda function as MCP tools**
through **Amazon Bedrock AgentCore Gateway** — plus detailed documentation
written for someone who knows AWS but has never heard of AgentCore.

The demo implements a small **order-management** service (six tools: look up
products, search the catalog, get/list/create/cancel orders) as a single Lambda,
and wires it up to a managed MCP endpoint with Cognito (OAuth2) inbound auth.

## What's in the box

```
├── README.md                     ← you are here
├── docs/
│   ├── 01-what-is-agentcore-gateway.md   concept & mental model (start here)
│   ├── 02-lambda-contract.md             the exact Lambda ⇄ Gateway interface
│   ├── 03-deployment-guide.md            step-by-step deploy (scripts + raw CLI)
│   ├── 04-testing-and-troubleshooting.md test recipes + failure matrix
│   └── architecture.md                   diagram + request lifecycle
├── src/lambda/                   the tool Lambda (Python, stdlib only)
│   ├── handler.py                gateway entry point: strips prefix, routes
│   ├── tools.py                  the six tool implementations + registry
│   └── orders_data.py            in-memory sample data (swap for a real store)
├── schemas/tool_schema.json      the tool contract (inline payload for target)
├── deploy/
│   ├── config.example.sh         copy → config.sh, fill in, source
│   ├── 10_deploy_lambda.sh       package + create/update the Lambda
│   ├── 20_setup_cognito.py       Cognito user pool for inbound auth
│   ├── 30_setup_gateway.py       gateway IAM role + gateway + Lambda target
│   ├── iam/                      trust & permission policy documents
│   └── cloudformation/           one-stack alternative to the scripts above
│       ├── agentcore-harness.yaml   full stack (Lambda, Cognito, gateway, target)
│       └── deploy_stack.sh          package + upload + deploy in one command
└── client/test_client.py         auth + list + call tools over MCP (smoke test)
```

## The idea in 30 seconds

An AI agent needs **tools**. The standard way to serve tools is an **MCP
server** — but running one (protocol, auth, scaling) is a chore. **AgentCore
Gateway is a managed MCP server you don't operate.** You write a Lambda; the
gateway turns it into MCP tools behind one authenticated HTTPS endpoint.

```
Agent ──MCP + Bearer JWT──▶ AgentCore Gateway ──InvokeFunction──▶ your Lambda
                             (validates the token)   (runs your tool code)
```

Two things make it work, and both are documented in detail in
[`docs/02-lambda-contract.md`](docs/02-lambda-contract.md):

1. **A tool schema** you register on the gateway (what the agent sees).
2. **An invocation contract** — the gateway passes the tool arguments as the
   Lambda `event`, and the tool name (prefixed `TargetName___tool`, with a
   **triple underscore**) in `context.client_context.custom`. Your handler
   strips the prefix and routes.

## Quickstart

Prereqs: AWS CLI v2 configured, Python 3.10+, `pip install boto3 requests`, and
a region where AgentCore is available (e.g. `us-east-1`).

```bash
# 0. configure
cp deploy/config.example.sh deploy/config.sh
$EDITOR deploy/config.sh          # set AWS_REGION + ACCOUNT_ID
source deploy/config.sh

# 1. deploy the Lambda
./deploy/10_deploy_lambda.sh

# 2. inbound auth (prints COGNITO_* exports → paste into config.sh, re-source)
python deploy/20_setup_cognito.py
source deploy/config.sh

# 3. gateway + target (prints GATEWAY_* exports → paste into config.sh, re-source)
python deploy/30_setup_gateway.py
source deploy/config.sh

# 4. end-to-end smoke test
pip install -r client/requirements.txt
python client/test_client.py
```

Expected: a token is fetched, `tools/list` shows six `OrderTools___*` tools, and
two tool calls return live JSON from the Lambda.

Full walkthrough with the raw AWS CLI/boto3 equivalent of every step:
[`docs/03-deployment-guide.md`](docs/03-deployment-guide.md).

**Prefer infrastructure-as-code?** `deploy/cloudformation/` builds the identical
harness as a single CloudFormation stack (using the native
`AWS::BedrockAgentCore::Gateway` / `::GatewayTarget` resource types) — see
[`deploy/cloudformation/README.md`](deploy/cloudformation/README.md).

## The tools

| Tool | Arguments | Returns |
|---|---|---|
| `get_product` | `product_id` | one product |
| `search_products` | `query`, `max_results?` | matching products |
| `get_order` | `order_id` | one order |
| `list_orders` | `customer_id`, `status?` | that customer's orders |
| `create_order` | `customer_id`, `items[]` | the created order |
| `cancel_order` | `order_id`, `reason?` | the updated order |

The data lives in `src/lambda/orders_data.py` as plain dicts. To make this real,
replace those with calls to DynamoDB / RDS / an internal API — nothing else
about the gateway integration changes.

## Where to start reading

- New to AgentCore? → [`docs/01-what-is-agentcore-gateway.md`](docs/01-what-is-agentcore-gateway.md)
- Just want the interface? → [`docs/02-lambda-contract.md`](docs/02-lambda-contract.md)
- Ready to deploy? → [`docs/03-deployment-guide.md`](docs/03-deployment-guide.md)
- Something broke? → [`docs/04-testing-and-troubleshooting.md`](docs/04-testing-and-troubleshooting.md)

## Notes & caveats

- The AgentCore Gateway API surface is evolving. This repo targets the
  `bedrock-agentcore-control` boto3 client and the `CUSTOM_JWT` inbound
  authorizer. If a field name differs in your boto3 version (e.g.
  `gatewayUrl` vs `gatewayEndpoint`), the scripts handle both where it matters —
  otherwise check `aws bedrock-agentcore-control help` and the linked docs.
- Sample data and tokens are for demonstration. Don't ship the in-memory store
  or commit a real `config.sh` (it's git-ignored for that reason).

## Reference documentation

- [AWS Lambda function targets — AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html)
- [Understand how AgentCore Gateway tools are named](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-tool-naming.html)
- [Set up inbound authorization for your gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)
- [Use an AgentCore gateway (MCP operations)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using.html)
- [MCP targets — AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-mcp.html)
- [Introducing Amazon Bedrock AgentCore Gateway (AWS ML blog)](https://aws.amazon.com/blogs/machine-learning/introducing-amazon-bedrock-agentcore-gateway-transforming-enterprise-ai-agent-tool-development/)
