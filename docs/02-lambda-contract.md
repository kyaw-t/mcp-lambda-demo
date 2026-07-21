# 2. The Lambda contract

This is the most important page. It defines the **exact interface** between
AgentCore Gateway and your Lambda function. Get this right and everything works;
get it wrong and you get "unknown tool" errors or malformed results.

There are two halves to the contract:

- **The tool schema** you register on the gateway target (what the agent sees).
- **The invocation format** the gateway uses when it calls your function (how
  your code figures out what to do).

---

## Part A — The tool schema (the contract the agent sees)

When you attach a Lambda target to a gateway you provide an **inline payload**:
a JSON array of **tool definitions**. Each definition is one tool the agent can
discover and call. This is what the gateway returns for `tools/list`, and it is
what the LLM reads to decide when and how to call a tool.

A single tool definition looks like this:

```json
{
  "name": "get_order",
  "description": "Retrieve a single order by its order_id.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "order_id": {
        "type": "string",
        "description": "The order identifier, e.g. 'O-5001'."
      }
    },
    "required": ["order_id"]
  }
}
```

Fields:

| Field | Required | Notes |
|---|---|---|
| `name` | ✅ | The tool name. Must match a key in your Lambda's routing table. |
| `description` | ✅ | The LLM reads this to decide when to use the tool. Write it well. |
| `inputSchema` | ✅ | A JSON-Schema **object** describing the arguments. |
| `outputSchema` | optional | JSON-Schema object describing the return shape. |

### The schema type system

`inputSchema` (and `outputSchema`) is an **object-type schema definition**:

```json
{
  "type": "object",
  "description": "optional",
  "properties": { "<name>": <SchemaDefinition>, ... },
  "required": ["<name>", ...]
}
```

Each property is itself a **SchemaDefinition**. Supported types:

| `type` | Extra fields |
|---|---|
| `string` | `description` |
| `number` | `description` |
| `integer` | `description` |
| `boolean` | `description` |
| `array` | `description`, `items` (a nested SchemaDefinition for each element) |
| `object` | `description`, `properties`, `required` (recursive) |

The `create_order` tool in this repo exercises the interesting cases — an
**array of objects**:

```json
"items": {
  "type": "array",
  "description": "The line items to order.",
  "items": {
    "type": "object",
    "properties": {
      "product_id": { "type": "string" },
      "quantity":   { "type": "integer" }
    },
    "required": ["product_id", "quantity"]
  }
}
```

> **Practical note on `enum`.** The documented SchemaDefinition covers the six
> types above and does not list `enum`. To stay strictly within the documented
> contract, this demo expresses "allowed values" in the property `description`
> (see `list_orders.status`) and validates them **in the Lambda code** rather
> than relying on the schema to reject bad values. Validate defensively in your
> function regardless — never assume the schema fully guards your inputs.

### Where the schema lives

You can supply the schema two ways when creating the target:

- **Inline** (`toolSchema.inlinePayload`) — the array pasted straight into the
  `create_gateway_target` call. This repo does that; the array is in
  `schemas/tool_schema.json` and read by `deploy/30_setup_gateway.py`.
- **From S3** (`toolSchema.s3`) — upload the JSON to a bucket and reference its
  location. Prefer this for large schemas or shared ownership.

---

## Part B — How the gateway invokes your Lambda

When an agent calls a tool, the gateway does an ordinary **synchronous Lambda
invoke** and passes two arguments: `event` and `context`.

### The `event` — just the arguments, no envelope

`event` is a plain dict: the tool's arguments, exactly matching the
`properties` of that tool's `inputSchema`. There is **no wrapper**. For
`get_order` the event is simply:

```json
{ "order_id": "O-5001" }
```

You do **not** get `event["arguments"]`, `event["body"]`, or a JSON-RPC
envelope. The arguments are the top-level event. That is why the handler treats
`event` itself as the argument dict:

```python
def lambda_handler(event, context):
    args = event if isinstance(event, dict) else {}
```

### The `context` — where the tool name lives

Because one Lambda backs many tools, you must find out **which** tool was
requested. The gateway puts that (and other metadata) into
`context.client_context.custom`:

| Key | Meaning |
|---|---|
| `bedrockAgentCoreToolName` | The tool that was called — **prefixed** (see below). |
| `bedrockAgentCoreGatewayId` | ID of the gateway. |
| `bedrockAgentCoreTargetId` | ID of the target. |
| `bedrockAgentCoreMessageVersion` | Message schema version, e.g. `"1.0"`. |
| `bedrockAgentCoreAwsRequestId` | Request ID into the AgentCore service. |
| `bedrockAgentCoreMcpMessageId` | The MCP message ID. |

### The tool-name prefix (the #1 gotcha)

The gateway namespaces every tool by its **target name** using a **triple
underscore** delimiter:

```
<target_name>___<tool_name>
```

So with a target named `OrderTools`, the tool your schema calls `get_order` is
visible to the agent — and arrives in your context — as:

```
OrderTools___get_order
```

**Your Lambda must strip that prefix** before routing. If you route on the raw
value you will never match your handler names. That is exactly what
`_resolve_tool_name` does:

```python
TOOL_NAME_DELIMITER = "___"

full_name = context.client_context.custom["bedrockAgentCoreToolName"]
tool_name = full_name.split(TOOL_NAME_DELIMITER, 1)[1]  # "OrderTools___get_order" -> "get_order"
```

### The response — return a plain value

Whatever your handler **returns** is serialized to JSON and delivered to the
agent as the tool result content. There is **no required MCP envelope** on the
way out — return a dict, list, or string:

```python
return {"order_id": "O-5001", "status": "SHIPPED", "total": 368.99}
```

The gateway wraps it into an MCP tool-result content block for you. On the
client side you read it back as text (see `client/test_client.py`).

### Errors

There is no formally documented error envelope, so this demo adopts a simple,
robust convention:

- **Expected, caller-facing problems** (order not found, insufficient stock,
  bad status filter) → return `{"error": "<message>"}`. The model reads the
  message and can react ("that order doesn't exist, want me to try another?").
- **Unexpected bugs** → the handler catches them, logs the stack trace to
  CloudWatch, and returns a generic `{"error": "Internal error ..."}` so
  internals never leak to the model or user. Raising instead would surface as a
  Lambda failure / MCP protocol error, which is harder for an agent to handle
  gracefully.

---

## Putting the two halves together

```
schemas/tool_schema.json          src/lambda/tools.py
─────────────────────────         ─────────────────────────
"name": "get_order"      ◀── must match ──▶  TOOL_REGISTRY["get_order"]
"inputSchema".properties ◀── must match ──▶  the args your function reads
```

The single rule to remember: **every `name` in the tool schema must have a
matching entry in your Lambda's routing table, and vice versa.** When they drift
apart you get `Unknown tool '...'` (schema has a tool the code doesn't) or a
tool the agent can never call (code has a handler the schema doesn't advertise).

Next: [3. Deployment guide](03-deployment-guide.md).

## Sources

- [AWS Lambda function targets — AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html)
- [Understand how AgentCore Gateway tools are named](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-tool-naming.html)
