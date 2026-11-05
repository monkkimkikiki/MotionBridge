"""AnimRef 핵심 로직 (GUI와 분리)."""


class Cancelled(Exception):
    """사용자가 작업을 취소했을 때."""


def noop(*_a, **_k):
    pass
