"""Utility package.

Keep package initialization dependency-free: ``config`` imports the secure-store
utility during database startup, while the client selector depends on models and
the database. Eagerly importing the selector here creates a circular import in a
fresh/frozen process.
"""

__all__ = ["get_selected_client", "render_client_selector"]


def __getattr__(name):
    """Preserve the old convenience exports without importing them eagerly."""
    if name in __all__:
        from .client_selector import get_selected_client, render_client_selector
        return {
            "get_selected_client": get_selected_client,
            "render_client_selector": render_client_selector,
        }[name]
    raise AttributeError(name)
