"""Support-ticket router."""


def route(ticket):
    category = ticket["category"]
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] >= 25:
        return "escalation"
    return "normal"
