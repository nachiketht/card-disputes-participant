"""Failures that stop a write.

MockUnavailable means the process has not started a case, so no file is written.
The other errors are per case: that case is not written, and the operator can
still run a different case.
"""


class CopilotError(Exception):
    pass


class MockUnavailable(CopilotError):
    pass


class ToolFailed(CopilotError):
    def __init__(self, status: int, payload: object, attempts: int):
        self.status = status
        self.payload = payload
        self.attempts = attempts
        super().__init__(
            f"tool failed with status {status} after {attempts} attempt(s); no amount was invented"
        )


class OutsideCheckpoint(CopilotError):
    pass


class LeakError(CopilotError):
    pass


class ContractError(CopilotError):
    pass


class CreateRejected(CopilotError):
    def __init__(self, status: int, payload: object):
        self.status = status
        self.payload = payload
        super().__init__(f"record was not created (status {status})")
