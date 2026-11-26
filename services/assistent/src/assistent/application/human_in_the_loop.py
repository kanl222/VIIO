"""Human-in-the-Loop (HITL) support using LangGraph interrupt() and Command(resume=...).

Halts execution for sensitive or destructive operations (e.g., writing to the shared
knowledge base, executing dangerous queries) and awaits interactive human confirmation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Set
from typing import Any

from langgraph.types import interrupt

logger = logging.getLogger(__name__)

# Default actions considered sensitive and requiring confirmation when HITL is enabled
HITL_SENSITIVE_ACTIONS: frozenset[str] = frozenset({
    "save_knowledge",
    "db_write",
    "critical_operation",
})

_AFFIRMATIVE_TOKENS: frozenset[str] = frozenset({
    "yes",
    "y",
    "true",
    "1",
    "approve",
    "approved",
    "confirm",
    "confirmed",
    "ok",
    "да",
    "д",
    "подтверждаю",
    "подтвердить",
    "разрешить",
    "согласен",
    "ок",
})

_CONFIRMATION_KEYS: tuple[str, ...] = (
    "approved",
    "confirmed",
    "decision",
    "status",
    "action",
)


def requires_confirmation(
    action: str,
    *,
    enabled: bool = True,
    sensitive_actions: Set[str] = HITL_SENSITIVE_ACTIONS,
) -> bool:
    """Checks whether the given action requires human confirmation."""
    if not enabled or not action:
        return False
    return action.strip() in sensitive_actions


def is_confirmed(response: Any) -> bool:
    """Evaluates whether the human approval response signifies confirmation or rejection."""
    if isinstance(response, bool):
        return response
    if response is None:
        return False
    if isinstance(response, int):
        return response == 1
    if isinstance(response, str):
        cleaned = response.strip().lower().rstrip(".!?,")
        return cleaned in _AFFIRMATIVE_TOKENS
    if isinstance(response, Mapping):
        for key in _CONFIRMATION_KEYS:
            if key in response:
                return is_confirmed(response[key])
        return False
    return False


def request_human_confirmation(
    action: str,
    details: Mapping[str, Any] | None = None,
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
    safe_details = dict(details) if details else {}
    resolved_prompt = prompt or f"Требуется подтверждение для действия: {action}"

    # Keep top-level compatibility while preventing 'details' from overwriting 'action' or 'prompt'
    payload: dict[str, Any] = {
        **safe_details,
        "action": action,
        "prompt": resolved_prompt,
        "details": safe_details,
    }
    logger.info(
        "Human-in-the-loop interruption requested: action=%s, details_keys=%s",
        action,
        list(safe_details.keys()),
    )
    return interrupt(payload)