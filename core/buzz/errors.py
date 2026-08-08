from __future__ import annotations


class BuzzAdapterError(RuntimeError):
    def __init__(self, code: str, *, retryable_before_attempt: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable_before_attempt = retryable_before_attempt
