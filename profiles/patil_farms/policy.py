"""Approval rules for Patil Farms. Called by the agent runtime BEFORE every write.

Return a reason string to require human approval, or None to allow the action.
Rules live in code (not the prompt) so the model cannot skip them.
"""


def requires_approval(tool: str, args: dict, profile: dict) -> str | None:
    if tool != "create_order":
        return None
    rules = profile["policy"]
    floors = rules.get("price_floor_per_kg", {})
    total = 0.0
    for item in args.get("items") or []:
        product = str(item.get("product", "")).lower()
        qty, price = float(item.get("qty_kg") or 0), float(item.get("price_per_kg") or 0)
        total += qty * price
        floor = floors.get(product)
        if floor is not None and price < floor:
            return f"Rs {price:g}/kg for {product} is below the Rs {floor:g}/kg price floor"
    limit = rules.get("approval_order_value_above")
    if limit is not None and total > limit:
        return f"Order value Rs {total:,.0f} is above the Rs {limit:,.0f} approval limit"
    return None
