"""AWS Lambda entry point for an Amazon Bedrock AgentCore Gateway target.

HOW THE GATEWAY CALLS THIS FUNCTION
-----------------------------------
When an agent invokes a tool through the gateway, AgentCore Gateway performs a
standard synchronous Lambda invocation and passes two things:

1. ``event``  -- a plain dict of the tool arguments. It is exactly the
   ``properties`` object from the tool's ``inputSchema``, already validated
   against the schema by the gateway. Example for ``get_order``:

       {"order_id": "O-5001"}

   Note there is NO envelope: the arguments are the top-level event. You do
   NOT get ``event["arguments"]`` or ``event["body"]`` -- just the arguments.

2. ``context`` -- the normal Lambda context object, but AgentCore injects
   metadata into ``context.client_context.custom``:

       bedrockAgentCoreToolName        e.g. "OrderTools___get_order"
       bedrockAgentCoreGatewayId
       bedrockAgentCoreTargetId
       bedrockAgentCoreMessageVersion
       bedrockAgentCoreAwsRequestId
       bedrockAgentCoreMcpMessageId

   The tool name is prefixed with "<target-name>___" (three underscores).
   We must strip that prefix to learn which tool was actually requested,
   because a single Lambda can back many tools.

WHAT WE RETURN
--------------
Whatever this handler returns is serialized to JSON and handed back to the
agent as the tool result content. Return a plain dict/list/str. There is no
required MCP envelope on the response side -- the gateway wraps it for you.
"""

import json
import logging

from tools import TOOL_REGISTRY, ToolInputError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# The gateway inserts "<target_name>___<tool_name>" as the visible tool name.
# The delimiter is three underscores.
TOOL_NAME_DELIMITER = "___"


def lambda_handler(event, context):
    tool_name = _resolve_tool_name(context)
    logger.info("Invoking tool '%s' with args: %s", tool_name, json.dumps(event))

    handler_fn = TOOL_REGISTRY.get(tool_name)
    if handler_fn is None:
        # Unknown tool -- almost always a mismatch between the tool schema
        # registered on the gateway target and this function's registry.
        return _error(
            f"Unknown tool '{tool_name}'. "
            f"Known tools: {', '.join(sorted(TOOL_REGISTRY))}."
        )

    # The event *is* the arguments dict. Guard against a non-dict just in case.
    args = event if isinstance(event, dict) else {}

    try:
        result = handler_fn(args)
    except ToolInputError as exc:
        # Expected, caller-facing validation problem -> clean tool error.
        logger.warning("Validation error in '%s': %s", tool_name, exc)
        return _error(str(exc))
    except Exception:  # noqa: BLE001 -- last-resort guard around tool code
        # Unexpected bug. Log the stack trace for operators, but return a
        # generic message so we never leak internals to the model/user.
        logger.exception("Unhandled error while executing tool '%s'", tool_name)
        return _error(f"Internal error while executing tool '{tool_name}'.")

    return result


def _resolve_tool_name(context):
    """Pull the tool name out of the AgentCore client context and strip the
    "<target_name>___" prefix so we get the bare tool name."""
    try:
        full_name = context.client_context.custom["bedrockAgentCoreToolName"]
    except (AttributeError, KeyError, TypeError):
        # This should only happen if the function is invoked outside of a
        # gateway (e.g. a manual test in the Lambda console with no client
        # context). Fall back to a sentinel so the caller gets a clear error.
        logger.error("No bedrockAgentCoreToolName in client context.")
        return "<missing>"

    if TOOL_NAME_DELIMITER in full_name:
        return full_name.split(TOOL_NAME_DELIMITER, 1)[1]
    return full_name


def _error(message):
    """Uniform shape for tool-level errors the model can read and act on."""
    return {"error": message}
