"""
Domain-specific exceptions.

Using typed exceptions instead of raw strings makes error handling explicit,
testable, and easy to map to HTTP status codes in routes.
"""


class PlanningError(Exception):
    """Base class for all application errors."""


class NotFoundError(PlanningError):
    """Raised when a requested resource does not exist."""


class ValidationError(PlanningError):
    """Raised when business validation rules are violated."""


class AuthorisationError(PlanningError):
    """Raised when a user lacks permission for an action."""


class DuplicateError(PlanningError):
    """Raised when attempting to create a resource that already exists."""


class CapacityError(PlanningError):
    """Raised when capacity constraints cannot be satisfied."""


class MaterialError(PlanningError):
    """Raised for material availability conflicts."""
