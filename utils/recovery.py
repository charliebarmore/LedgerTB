"""Plain recovery guidance without echoing database paths or raw exception text."""

from database import connection as dbconn


def save_error_message(exc):
    text = str(exc).lower()
    if dbconn.READ_ONLY or "readonly" in text or "not authorized" in text:
        return "This book cannot be changed in this session. Reopen it with editing access, then try again."
    if "locked" in text or "busy" in text:
        return (
            "The book is busy. Wait for the other operation to finish, then try again."
        )
    if isinstance(exc, ValueError):
        if "closed" in text:
            return "The date falls in a closed period. Correct the date or reopen the period before posting."
        if any(
            word in text
            for word in ("duplicate", "previously imported", "dismissed", "superseded")
        ):
            return "This row has existing import history. Review that history before posting it again."
        return "Check the date, amount and account choices, then try again."
    if isinstance(exc, OSError):
        return "The change could not be saved. Check that the book folder is writable and has free space, then try again."
    return "The change could not be saved. Keep this window open and try again."
