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


class ProviderDisconnected(Exception):
    """传输层在拿到响应前断开（连接被重置、上游直接断流、代理抖动）。

    与超时分开是为了能重试：代理或上游偶尔会不发响应就断开连接，
    这类失败重试一次通常就好，不应该冒泡成一个 500。
    """
