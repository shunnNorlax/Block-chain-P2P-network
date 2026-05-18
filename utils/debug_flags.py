"""
Runtime debug toggle shared across modules.
"""

DEBUG_ENABLED = False


def set_debug_enabled(enabled: bool) -> None:
    global DEBUG_ENABLED
    DEBUG_ENABLED = bool(enabled)


def is_debug_enabled() -> bool:
    return DEBUG_ENABLED


def debug_print(*args, **kwargs) -> None:
    if DEBUG_ENABLED:
        print(*args, **kwargs)