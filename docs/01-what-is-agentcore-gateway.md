# 1. What is Amazon Bedrock AgentCore Gateway?

> Audience: you know AWS (Lambda, IAM, Cognito) but have never heard of
> AgentCore. This page gives you the mental model before you touch any code.

## The problem it solves

AI agents (LLM-driven applications) call **tools** to get things done: look up
an order, query a database, hit an internal API. The emerging standard for how
an agent discovers and calls tools is the **Model Context Protocol (MCP)** — a
JSON-RPC protocol where a "client" (the agent) talks to a "server" (the thing
that hosts tools).

Normally, to give an agent a tool you would have to:

- stand up and operate an MCP server (a long-running process speaking the MCP
  wire protocol),
- put authentication in front of it,
- keep it patched, scaled, and monitored.

**AgentCore Gateway is a fully managed MCP server that you don't run.** You
point it at your existing AWS resources — most commonly **Lambda functions** —
and it exposes them to agents as MCP tools over a single HTTPS endpoint. It
handles the MCP protocol, authentication, and the translation from "agent calls
a tool" into "invoke your Lambda."

```
Without a gateway:  you build + run an MCP server, wire in auth, scale it.
With the gateway:   you write a Lambda; the gateway is the MCP server.
```

## The three things a gateway ties together

Think of a gateway as a managed object with three parts:

1. **A protocol front door (MCP).** Agents connect to one HTTPS URL — the
   gateway's *MCP endpoint* — and speak MCP over
   [streamable HTTP](https://modelcontextprotocol.io/). Through it they can
   `tools/list` (discover tools) and `tools/call` (invoke one).

2. **Inbound authorization.** Who is allowed to call the gateway. The gateway
   speaks the MCP authorization spec, which is OAuth2 bearer tokens (JWT). You
   bring an OAuth identity provider — **Amazon Cognito**, Okta, Auth0, Entra ID,
   or your own — and the gateway validates every incoming token against it. (An
   IAM/SigV4 mode also exists; this demo uses Cognito JWT because that is what
   most agent frameworks expect.)

3. **Targets + outbound authorization.** A **target** is a backend the gateway
   turns into tools. This demo uses a **Lambda target**. Each target carries a
   **tool schema** — the list of tools, their descriptions, and their input
   schemas. For Lambda targets the gateway invokes your function using its own
   IAM role (**outbound auth = `GATEWAY_IAM_ROLE`**), so the gateway's role
   needs `lambda:InvokeFunction` on your function.

```
   Agent ──MCP over HTTPS──▶  ┌───────────────────────────────┐
      (Bearer JWT)            │        AgentCore Gateway       │
                              │                                │
   1. inbound auth  ────────▶ │  validate JWT via Cognito      │
                              │                                │
   3. target + schema ──────▶ │  route tool call ──┐           │
                              └────────────────────┼───────────┘
                                                   │ lambda:InvokeFunction
                                    (gateway IAM role assumes, SigV4)
                                                   ▼
                                          Your Lambda function
```

## Why back it with Lambda specifically

A Lambda target is the sweet spot when your tools are **custom code**: you want
to run arbitrary logic, call internal systems, transform data — anything you can
express in a function. The gateway supports other target types too (OpenAPI
specs, Smithy models, existing MCP servers), but Lambda is the most flexible and
the one with the least standing infrastructure. You write a function; there is
no server to operate.

Key fact that shapes everything in this repo: **one Lambda can back many
tools.** The gateway tells your function *which* tool was called (via the
invocation context), and your function routes to the right logic. So the unit of
work is "a Lambda that implements a family of related tools," not "a Lambda per
tool."

## What you are responsible for vs. what the gateway does

| Concern | You | Gateway (managed) |
|---|---|---|
| MCP wire protocol | — | ✅ |
| TLS endpoint, scaling of the front door | — | ✅ |
| Validating inbound JWTs | Configure the IdP | ✅ Enforces it |
| Deciding which tool ran | — | ✅ Passes tool name |
| Tool business logic | ✅ Your Lambda | — |
| Tool schema (contract) | ✅ You author it | ✅ Serves it on `tools/list` |
| Invoking the Lambda | — | ✅ Using its IAM role |
| IAM role + permissions | ✅ You create them | ✅ Assumes the role |

## The pieces you will create in this repo

1. A **Lambda function** implementing six order-management tools
   (`src/lambda/`).
2. A **tool schema** describing those tools (`schemas/tool_schema.json`).
3. A **Cognito** user pool for inbound auth (`deploy/20_setup_cognito.py`).
4. A **gateway IAM role** + the **gateway** + a **Lambda target**
   (`deploy/30_setup_gateway.py`).
5. A **test client** that authenticates and calls the tools
   (`client/test_client.py`).

Continue to [2. The Lambda contract](02-lambda-contract.md) to see exactly how
the gateway and your function talk to each other, then
[3. Deployment guide](03-deployment-guide.md) to build it.

## Sources

- [MCP targets — AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-mcp.html)
- [AWS Lambda function targets — AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html)
- [Set up inbound authorization — AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)
- [Introducing Amazon Bedrock AgentCore Gateway (AWS ML blog)](https://aws.amazon.com/blogs/machine-learning/introducing-amazon-bedrock-agentcore-gateway-transforming-enterprise-ai-agent-tool-development/)
