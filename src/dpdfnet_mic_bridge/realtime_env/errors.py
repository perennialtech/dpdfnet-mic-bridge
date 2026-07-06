"""Realtime setup exceptions."""


class RealtimeSetupError(RuntimeError):
    """Raised when strict realtime setup cannot be satisfied."""


class PersistentSetupError(RuntimeError):
    """Raised when persistent realtime setup fails."""
