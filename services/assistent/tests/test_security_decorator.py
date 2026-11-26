"""Unit tests for assistent.api.security module and role decorators."""

import pytest
from assistent.api.security import (
    DEFAULT_ACCESS_DENIED_MESSAGE,
    ensure_role_access,
    has_role_access,
    require_roles,
)


def _run_coro(coro):
    """Run a simple async coroutine without requiring event loop network socketpair."""
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    raise RuntimeError("Coroutine did not finish on first step")


def test_has_role_access():
    assert has_role_access({"user_id": "u1", "role": "admin"}, ["admin", "programmer"])
    assert has_role_access({"user_id": "u1", "role": "programmer"}, ["programmer"])
    assert not has_role_access({"user_id": "u1", "role": "employee"}, ["admin", "programmer"])
    assert not has_role_access({"role": "admin"}, ["admin"])
    assert not has_role_access({"user_id": ""}, ["admin"])
    assert not has_role_access(None, ["admin"])


def test_ensure_role_access():
    ensure_role_access({"user_id": "u1", "role": "admin"}, ["admin"])
    with pytest.raises(PermissionError, match="Custom error"):
        ensure_role_access({"user_id": "u1", "role": "guest"}, ["admin"], "Custom error")


def test_require_roles_tool_decorator_async():
    @require_roles(["admin", "programmer"], "Отказано в доступе")
    async def sample_tool(param: str, config: dict) -> str:
        """Tool docstring."""
        return f"result:{param}"

    assert sample_tool.__name__ == "sample_tool"
    assert sample_tool.__doc__ == "Tool docstring."

    ok_config = {"configurable": {"user_context": {"user_id": "u1", "role": "programmer"}}}
    bad_config = {"configurable": {"user_context": {"user_id": "u1", "role": "employee"}}}
    no_user_config = {"configurable": {}}

    assert _run_coro(sample_tool("test", config=ok_config)) == "result:test"
    assert _run_coro(sample_tool("test", config=bad_config)) == "Отказано в доступе"
    assert _run_coro(sample_tool("test", config=no_user_config)) == "Отказано в доступе"


def test_require_roles_callable_roles():
    current_roles = ["admin"]

    @require_roles(lambda: current_roles, "Только для текущих ролей")
    async def dynamic_tool(config: dict) -> str:
        return "allowed"

    cfg_admin = {"configurable": {"user_context": {"user_id": "u", "role": "admin"}}}
    cfg_prog = {"configurable": {"user_context": {"user_id": "u", "role": "programmer"}}}

    assert _run_coro(dynamic_tool(config=cfg_admin)) == "allowed"
    assert _run_coro(dynamic_tool(config=cfg_prog)) == "Только для текущих ролей"

    current_roles.append("programmer")
    assert _run_coro(dynamic_tool(config=cfg_prog)) == "allowed"


def test_require_roles_raise_exception():
    @require_roles(["admin"], "Нет прав", raise_exception=True)
    def admin_action(user_context: dict) -> str:
        return "action_done"

    assert admin_action(user_context={"user_id": "u", "role": "admin"}) == "action_done"
    with pytest.raises(PermissionError, match="Нет прав"):
        admin_action(user_context={"user_id": "u", "role": "guest"})
