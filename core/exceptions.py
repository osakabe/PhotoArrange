class PhotoArrangeError(Exception):
    """Base exception for all PhotoArrange errors."""

    pass


class DatabaseError(PhotoArrangeError):
    """Raised when a database operation fails."""

    pass


class DatabaseLockedError(DatabaseError):
    """Raised when the database is locked (busy)."""

    pass


class AIModelError(PhotoArrangeError):
    """Raised when an AI model fails to load or infer."""

    pass


class ResourceNotFoundError(PhotoArrangeError):
    """Raised when a file, record, or model is not found."""

    pass


class ConfigurationError(PhotoArrangeError):
    """Raised when there is an issue with application settings."""

    pass
