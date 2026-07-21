"""Tool implementations for the demo Order Management MCP server.

Each function here implements ONE tool. They take a plain ``dict`` of
arguments (exactly what the model supplied, already matching the tool's
``inputSchema``) and return a plain JSON-serializable Python value
(``dict``, ``list``, ``str``, ``int``, ...).

Keep these functions free of any AgentCore/Lambda specifics -- they are just
business logic. The routing layer in ``handler.py`` is responsible for
translating a gateway invocation into a call to one of these functions.

Convention used in this demo:
    * On success, return the useful result object.
    * On an *expected* failure (order not found, bad status, ...), return a
      dict shaped like ``{"error": "<message>"}``. The model sees this text
      and can react to it. We deliberately do NOT raise for these, so the
      agent gets a clean, actionable message instead of a generic 500.
    * For *programming* errors (a bug), let the exception propagate -- the
      handler converts it into a structured error for the gateway.
"""

from orders_data import ORDERS, PRODUCTS, VALID_STATUSES


class ToolInputError(ValueError):
    """Raised when the caller supplied arguments that fail validation.

    The handler turns this into a clean tool-level error message rather than
    a Lambda crash.
    """


# --------------------------------------------------------------------------
# Product tools
# --------------------------------------------------------------------------
def get_product(args):
    product_id = _require_str(args, "product_id")
    product = PRODUCTS.get(product_id)
    if product is None:
        return {"error": f"No product found with product_id '{product_id}'."}
    return product


def search_products(args):
    query = _require_str(args, "query").lower()
    max_results = args.get("max_results", 10)
    if not isinstance(max_results, int) or max_results <= 0:
        raise ToolInputError("'max_results' must be a positive integer.")

    matches = [
        p
        for p in PRODUCTS.values()
        if query in p["name"].lower() or query in p["category"].lower()
    ]
    return {
        "query": query,
        "count": len(matches[:max_results]),
        "results": matches[:max_results],
    }


# --------------------------------------------------------------------------
# Order tools
# --------------------------------------------------------------------------
def get_order(args):
    order_id = _require_str(args, "order_id")
    order = ORDERS.get(order_id)
    if order is None:
        return {"error": f"No order found with order_id '{order_id}'."}
    return order


def list_orders(args):
    customer_id = _require_str(args, "customer_id")
    status = args.get("status")
    if status is not None:
        status = str(status).upper()
        if status not in VALID_STATUSES:
            raise ToolInputError(
                f"'status' must be one of {', '.join(VALID_STATUSES)}."
            )

    results = [
        o
        for o in ORDERS.values()
        if o["customer_id"] == customer_id
        and (status is None or o["status"] == status)
    ]
    return {
        "customer_id": customer_id,
        "status_filter": status,
        "count": len(results),
        "orders": results,
    }


def create_order(args):
    customer_id = _require_str(args, "customer_id")
    items = args.get("items")
    if not isinstance(items, list) or not items:
        raise ToolInputError("'items' must be a non-empty array of line items.")

    total = 0.0
    normalized_items = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ToolInputError(f"items[{i}] must be an object.")
        product_id = item.get("product_id")
        quantity = item.get("quantity", 1)
        product = PRODUCTS.get(product_id)
        if product is None:
            return {"error": f"Unknown product_id '{product_id}' in items[{i}]."}
        if not isinstance(quantity, int) or quantity <= 0:
            raise ToolInputError(f"items[{i}].quantity must be a positive integer.")
        if quantity > product["in_stock"]:
            return {
                "error": (
                    f"Insufficient stock for '{product_id}': "
                    f"requested {quantity}, only {product['in_stock']} available."
                )
            }
        total += product["price"] * quantity
        normalized_items.append({"product_id": product_id, "quantity": quantity})

    # A real implementation would persist this and generate a durable ID.
    new_id = f"O-{5000 + len(ORDERS) + 1}"
    order = {
        "order_id": new_id,
        "customer_id": customer_id,
        "status": "PENDING",
        "items": normalized_items,
        "total": round(total, 2),
        "created_at": "2026-07-21T00:00:00Z",  # static for a deterministic demo
    }
    ORDERS[new_id] = order
    return order


def cancel_order(args):
    order_id = _require_str(args, "order_id")
    reason = args.get("reason")
    order = ORDERS.get(order_id)
    if order is None:
        return {"error": f"No order found with order_id '{order_id}'."}
    if order["status"] in ("DELIVERED", "CANCELLED"):
        return {
            "error": (
                f"Order '{order_id}' is {order['status']} and cannot be cancelled."
            )
        }
    order["status"] = "CANCELLED"
    if reason:
        order["cancellation_reason"] = reason
    return order


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _require_str(args, key):
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"Missing or empty required argument '{key}'.")
    return value


# --------------------------------------------------------------------------
# Tool registry: maps the *tool name* (as defined in the tool schema) to the
# function that implements it. The handler uses this to route invocations.
# The keys here MUST match the "name" fields in schemas/tool_schema.json.
# --------------------------------------------------------------------------
TOOL_REGISTRY = {
    "get_product": get_product,
    "search_products": search_products,
    "get_order": get_order,
    "list_orders": list_orders,
    "create_order": create_order,
    "cancel_order": cancel_order,
}
