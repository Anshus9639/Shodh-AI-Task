"""Router for the support-ticket workflow."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] > 30:
        return "escalation"
    if "category" in ticket and ticket["category"] == "technical":
        return "legacy"
    return "normal"
