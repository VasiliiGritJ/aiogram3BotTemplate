"""Small, fail-closed authorization boundary for privileged Telegram flows."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from storage.config import admins


Params = ParamSpec("Params")
Result = TypeVar("Result")
DENIAL_TEXT = "Действие недоступно."


def is_authorized_admin(user_id: object, admin_ids: object | None = None) -> bool:
    """Accept only a positive integer explicitly configured server-side."""
    if type(user_id) is not int or user_id <= 0:
        return False
    configured_ids = admins if admin_ids is None else admin_ids
    if not isinstance(configured_ids, (list, tuple, set, frozenset)):
        return False
    return any(type(value) is int and value == user_id for value in configured_ids)


def require_admin_callback(
    handler: Callable[Params, Awaitable[Result]],
) -> Callable[Params, Awaitable[Result | None]]:
    """Guard every privileged callback independently of its payload or FSM."""

    @wraps(handler)
    async def guarded(call: Any, state: Any, *args: Params.args, **kwargs: Params.kwargs) -> Result | None:
        user_id = getattr(getattr(call, "from_user", None), "id", None)
        if not is_authorized_admin(user_id):
            await _clear_state(state)
            await call.answer(DENIAL_TEXT, show_alert=True)
            return None
        return await handler(call, state, *args, **kwargs)

    return guarded


def require_admin_message(
    handler: Callable[Params, Awaitable[Result]],
) -> Callable[Params, Awaitable[Result | None]]:
    """Guard every privileged FSM message step independently."""

    @wraps(handler)
    async def guarded(message: Any, state: Any, *args: Params.args, **kwargs: Params.kwargs) -> Result | None:
        user_id = getattr(getattr(message, "from_user", None), "id", None)
        if not is_authorized_admin(user_id):
            await _clear_state(state)
            await message.answer(DENIAL_TEXT)
            return None
        return await handler(message, state, *args, **kwargs)

    return guarded


async def _clear_state(state: Any) -> None:
    try:
        await state.clear()
    except Exception:
        # Denial remains fail-closed even when a stale FSM storage is unavailable.
        pass
