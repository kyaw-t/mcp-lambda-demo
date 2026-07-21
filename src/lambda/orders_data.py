"""In-memory sample data for the demo Order Management tools.

In a real deployment you would replace these dictionaries with calls to a
datastore (DynamoDB, RDS, an internal API, etc.). Everything else in the
Lambda stays the same -- the tool routing and the schema contract with the
AgentCore Gateway are independent of where the data actually lives.
"""

# --------------------------------------------------------------------------
# Products catalog
# --------------------------------------------------------------------------
PRODUCTS = {
    "P-1001": {
        "product_id": "P-1001",
        "name": "Wireless Noise-Cancelling Headphones",
        "category": "electronics",
        "price": 249.99,
        "in_stock": 42,
    },
    "P-1002": {
        "product_id": "P-1002",
        "name": "Mechanical Keyboard (Brown switches)",
        "category": "electronics",
        "price": 119.00,
        "in_stock": 17,
    },
    "P-1003": {
        "product_id": "P-1003",
        "name": "Stainless Steel Water Bottle 1L",
        "category": "kitchen",
        "price": 24.50,
        "in_stock": 0,
    },
    "P-1004": {
        "product_id": "P-1004",
        "name": "Ergonomic Office Chair",
        "category": "furniture",
        "price": 389.00,
        "in_stock": 8,
    },
}

# --------------------------------------------------------------------------
# Orders. `status` is one of: PENDING, SHIPPED, DELIVERED, CANCELLED
# --------------------------------------------------------------------------
ORDERS = {
    "O-5001": {
        "order_id": "O-5001",
        "customer_id": "C-900",
        "status": "SHIPPED",
        "items": [
            {"product_id": "P-1001", "quantity": 1},
            {"product_id": "P-1002", "quantity": 1},
        ],
        "total": 368.99,
        "created_at": "2026-07-10T14:03:00Z",
    },
    "O-5002": {
        "order_id": "O-5002",
        "customer_id": "C-900",
        "status": "DELIVERED",
        "items": [{"product_id": "P-1004", "quantity": 2}],
        "total": 778.00,
        "created_at": "2026-06-28T09:20:00Z",
    },
    "O-5003": {
        "order_id": "O-5003",
        "customer_id": "C-901",
        "status": "PENDING",
        "items": [{"product_id": "P-1003", "quantity": 3}],
        "total": 73.50,
        "created_at": "2026-07-19T18:45:00Z",
    },
}

VALID_STATUSES = ("PENDING", "SHIPPED", "DELIVERED", "CANCELLED")
