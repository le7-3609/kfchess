from typing import Generic, Optional, TypeVar

T = TypeVar('T')
E = TypeVar('E')


class Result(Generic[T, E]):
    """A clean value-returning structure for success/failure handling."""

    def __init__(self, is_ok: bool, value: Optional[T] = None, error: Optional[E] = None) -> None:
        self.is_ok = is_ok
        self._value = value
        self._error = error

    @property
    def value(self) -> T:
        if not self.is_ok:
            raise ValueError(f"Cannot retrieve value from a failed Result: {self._error}")
        return self._value  # type: ignore[return-value]

    @property
    def error(self) -> E:
        if self.is_ok:
            raise ValueError("Cannot retrieve error from a successful Result")
        return self._error  # type: ignore[return-value]

    @classmethod
    def ok(cls, value: T) -> 'Result[T, E]':
        return cls(is_ok=True, value=value)

    @classmethod
    def fail(cls, error: E) -> 'Result[T, E]':
        return cls(is_ok=False, error=error)
