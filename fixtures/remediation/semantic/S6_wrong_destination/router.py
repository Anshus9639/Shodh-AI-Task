"""Support-ticket router returning the wrong urgent destination."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "manual"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
