"""Domain-specific exceptions for the LandValue360 calculation kernel."""

from __future__ import annotations


class LandValue360Error(Exception):
    """Base class for all kernel exceptions."""


class InputValidationError(LandValue360Error):
    """Raised when an input bundle violates a mandatory invariant."""

    def __init__(self, message: str, *, path: str | None = None, code: str = "INPUT_INVALID") -> None:
        super().__init__(message)
        self.path = path
        self.code = code


class CalculationError(LandValue360Error):
    """Raised when a calculation cannot be completed safely."""


class UnsupportedMethodError(LandValue360Error):
    """Raised when the caller requests a method outside the current release scope."""
