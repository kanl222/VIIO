"""Модуль проверки прав доступа и ролей пользователей.

Содержит универсальные функции и декораторы проверки ролей для инструментов
агента (LangChain RunnableConfig) и сервисных методов.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterable
from typing import Any


DEFAULT_ACCESS_DENIED_MESSAGE = "Доступ запрещён."


def has_role_access(context: dict | None, allowed_roles: Iterable[str]) -> bool:
    """Проверяет наличие доступа у пользователя по переданному контексту.

    Args:
        context: Словарь контекста пользователя (user_id, role, departments).
        allowed_roles: Набор допустимых ролей.

    Returns:
        True, если user_id задан и роль входит в allowed_roles; иначе False.
    """
    if not context or not context.get("user_id"):
        return False
    role = context.get("role")
    if not role:
        return False
    # Программисты и администраторы имеют права сотрудников (employee) по умолчанию
    effective_roles = {role, "employee"} if role in {"admin", "programmer"} else {role}
    return bool(effective_roles.intersection(allowed_roles))


def ensure_role_access(
    context: dict | None,
    allowed_roles: Iterable[str],
    error_message: str = DEFAULT_ACCESS_DENIED_MESSAGE,
) -> None:
    """Проверяет доступ пользователя и вызывает PermissionError при отказе.

    Args:
        context: Словарь контекста пользователя.
        allowed_roles: Набор допустимых ролей.
        error_message: Текст исключения при отказе в доступе.

    Raises:
        PermissionError: Если доступ отклонён.
    """
    if not has_role_access(context, allowed_roles):
        raise PermissionError(error_message)


def _extract_user_context(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict | None:
    """Извлекает контекст пользователя из аргументов функции или RunnableConfig."""
    if "user_context" in kwargs:
        return kwargs["user_context"]
    if "config" in kwargs and isinstance(kwargs["config"], dict):
        return kwargs["config"].get("configurable", {}).get("user_context")

    for arg in args:
        if isinstance(arg, dict):
            # Проверяем RunnableConfig LangChain
            configurable = arg.get("configurable")
            if isinstance(configurable, dict) and "user_context" in configurable:
                return configurable.get("user_context")
            # Проверяем прямой user_context словарь
            if "role" in arg and ("user_id" in arg or "departments" in arg):
                return arg
    return None


def require_roles(
    roles: Iterable[str] | Callable[[], Iterable[str]],
    error_message: str = DEFAULT_ACCESS_DENIED_MESSAGE,
    raise_exception: bool = False,
):
    """Декоратор проверки роли пользователя для инструментов LangChain и сервисных функций.

    Args:
        roles: Коллекция разрешённых ролей либо фабрика/callable, возвращающая коллекцию.
        error_message: Сообщение об ошибке при отсутствии прав.
        raise_exception: Если True — возбуждает PermissionError; если False (по умолчанию
            для инструментов агента) — возвращает строку с сообщением об ошибке.

    Returns:
        Декоратор функции.
    """
    def decorator(fn: Callable) -> Callable:
        def _get_allowed() -> set[str]:
            items = roles() if callable(roles) else roles
            return set(items)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                context = _extract_user_context(args, kwargs)
                if not has_role_access(context, _get_allowed()):
                    if raise_exception:
                        raise PermissionError(error_message)
                    return error_message
                return await fn(*args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                context = _extract_user_context(args, kwargs)
                if not has_role_access(context, _get_allowed()):
                    if raise_exception:
                        raise PermissionError(error_message)
                    return error_message
                return fn(*args, **kwargs)
            return sync_wrapper

    return decorator
