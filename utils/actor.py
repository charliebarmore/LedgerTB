"""Who is doing the work, for audit and activity attribution.

ProBooks has no login of its own — the operating system already knows who is
signed in, so the actor is the macOS account's full name (falling back to the
short username). Captured once per process.
"""
import functools
import getpass
import os


@functools.lru_cache(maxsize=1)
def current_actor() -> str:
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
