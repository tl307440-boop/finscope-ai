"""运行时配置的加解密。

主密钥取 ``SETTINGS_ENCRYPTION_KEY``，没设置就退回 ``JWT_SECRET``。
换了主密钥后旧密文解不开：这时按「未配置」处理并提示重新填写，不会抛出明文或崩溃。
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

_SALT = "finscope-settings-v1|"


class SecretBox:
    def __init__(self, master: str) -> None:
        if not master:
            raise ValueError("加密主密钥不能为空")
        digest = hashlib.sha256((_SALT + master).encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def seal(self, text: str) -> str:
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def open(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError):
            return None
