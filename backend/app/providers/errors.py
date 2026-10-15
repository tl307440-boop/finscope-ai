class ProviderError(Exception):
    """上游失败。调用方不得据此删除已保存的数据。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.attempts = attempts


class ProviderTimeout(Exception):
    """传输层超时，重试耗尽后转为 ProviderError。"""
