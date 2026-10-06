"""Built-in mock tool implementations for the demo tenant and mock mode.

The engine applies the simulated delay so the 400ms filler path can be
exercised end to end (set VA_MOCK_TOOL_DELAY_MS > VA_FILLER_AFTER_MS).
"""

import asyncio

MOCK_TOOLS = {"get_order_status", "book_appointment", "lookup_customer"}


async def run_mock_tool(tool_name: str, arguments: dict, delay_ms: int) -> dict:
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
    if tool_name == "get_order_status":
        return {
            "order_id": arguments.get("order_id", "unknown"),
            "status": "shipped",
            "eta": "Friday",
        }
    if tool_name == "book_appointment":
        return {"confirmation_id": "BK-1234", "slot": arguments.get("slot", "next available")}
    if tool_name == "lookup_customer":
        return {
            "name": "Demo Customer",
            "phone": arguments.get("phone", ""),
            "tier": "gold",
        }
    return {"result": f"mock ok: {tool_name}", "arguments": arguments}
