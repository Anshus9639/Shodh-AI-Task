"""Support-ticket router."""


def route(ticket):
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "category" not in ticket:
        return "manual"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
