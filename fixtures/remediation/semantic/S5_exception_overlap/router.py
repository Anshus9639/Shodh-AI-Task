"""Support-ticket router with the urgent/vip exception."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if (
        "priority" in ticket
        and ticket["priority"] == "urgent"
        and "vip" in ticket
        and ticket["vip"]
    ):
        return "manual"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
