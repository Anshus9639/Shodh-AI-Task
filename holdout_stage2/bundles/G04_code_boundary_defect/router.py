"""Linked router for the support-ticket workflow."""

DEFAULT_DESTINATION = "normal_queue"


def route(ticket):
    if "category" not in ticket or ticket.get("category") is None:
        return "manual_review"
    if ticket.get("priority") == "urgent":
        return "priority_queue"
    if ticket.get("wait_time") is not None and ticket.get("wait_time") > 30:
        return "escalation_queue"
    return DEFAULT_DESTINATION
