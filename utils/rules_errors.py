"""Shared user-facing error type for the game rules feature."""


class RulesError(Exception):
    """A rules failure whose message is safe to show to the user."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message
