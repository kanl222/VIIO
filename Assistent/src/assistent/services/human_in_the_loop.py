"""Human-in-the-Loop (HITL) support using LangGraph interrupt() and Command(resume=...).

Halts execution for sensitive or destructive operations (e.g., writing to the shared
knowledge base, executing dangerous queries) and awaits interactive human confirmation.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

logger = logging.getLogger(__name__)

# Default actions considered sensitive and requiring confirmation when HITL is enabled
HITL_SENSITIVE_ACTIONS = frozenset({
    "save_knowledge",
    "db_write",
    "critical_operation",
})


def is_confirmed(response: Any) -> bool:
    """Evaluates whether the human approval response signifies confirmation or rejection."""
    if response is True:
        return True
    if response is False or response is None:
        return False
    if isinstance(response, dict):
        return bool(response.get("approved") or response.get("confirmed"))
    if isinstance(response, str):
        cleaned = response.strip().lower()
        return cleaned in {"yes", "да", "подтверждаю", "разрешить", "согласен", "ок", "ok"}
    return False


def request_human_confirmation(
    action: str,
    details: dict[str, Any] | None = None,
    prompt: str | None = None,
) -> Any:
    """Halts the LangGraph workflow at the current checkpoint and requests user confirmation.

    Args:
        action: Identifier of the action requiring approval (e.g. 'save_knowledge').
        details: Payload describing what is about to be executed.
        prompt: User-friendly Russian prompt describing the confirmation request.

    Returns:
        The resume value supplied by the user via Command(resume=...).
    """
    payload = {
        "action": action,
        "prompt": prompt or f"Требуется подтверждение для действия: {action}",
        **(details or {}),
    }
    logger.info("Human-in-the-loop interruption requested: action=%s, details=%s", action, details)
    return interrupt(payload)
