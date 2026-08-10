"""Who is doing the work, for audit and activity attribution.

LedgerTB has no login of its own — the operating system already knows who is
signed in, so the actor is the OS account's full name (falling back to the
short username). Captured once per process.

A process acting FOR the user rather than AS the user — the MCP server — calls
mark_as_assistant() once at startup, and every actor stamp it writes carries
an "(AI)" suffix. The audit trail and activity feed then distinguish "Charlie
Barmore" from "Charlie Barmore (AI)" without inventing a login system, the
same convention LedgerPDF uses for its CJB (AI) marks.
"""
import functools
import getpass
import os

_ASSISTANT = False


def mark_as_assistant() -> None:
    """Stamp every actor attribution from this process as assistant work."""
    global _ASSISTANT
    _ASSISTANT = True


@functools.lru_cache(maxsize=1)
def _os_actor() -> str:
    try:
        import pwd

        full_name = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
        if full_name:
            return full_name
    except Exception:
        pass
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def current_actor() -> str:
    return f"{_os_actor()} (AI)" if _ASSISTANT else _os_actor()
