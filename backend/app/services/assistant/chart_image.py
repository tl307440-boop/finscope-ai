"""走势图附件：校验字节、落到已有的上传目录，按用户读回。

路径只用用户 id 和回合 id，不用原始文件名。文件放在 `data/uploads/<用户>/charts/`，
这样会跟着现有的上传卷一起保留，解析 PDF 的任务不会把它当成文档。
"""

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

MAX_CHART_BYTES = 4 * 1024 * 1024

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF = (b"GIF87a", b"GIF89a")


class ChartRejected(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ChartImage:
    media_type: str
    data: bytes

    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.media_type};base64,{encoded}"


def sniff_media(data: bytes) -> str | None:
    """只认文件头。类型对不上就当不是走势图，不按扩展名放行。"""

    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_GIF):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_chart(media_type: str, data_base64: str) -> ChartImage:
    compact = "".join(data_base64.split())
    try:
        data = base64.b64decode(compact, validate=True)
    except binascii.Error as exc:
        raise ChartRejected("走势图不是有效的图片数据") from exc
    if not data or len(data) > MAX_CHART_BYTES:
        raise ChartRejected("走势图为空或超过 4 MB")
    sniffed = sniff_media(data)
    if sniffed is None or sniffed != media_type:
        raise ChartRejected("只接受 png、jpg、webp、gif 走势图")
    return ChartImage(media_type=sniffed, data=data)


def chart_path(root: Path, user_id: UUID, client_message_id: UUID) -> Path:
    base = root.resolve()
    path = (base / str(user_id) / "charts" / str(client_message_id)).resolve()
    if not path.is_relative_to(base):
        raise ChartRejected("走势图路径无效")
    return path


def save_chart(root: Path, user_id: UUID, client_message_id: UUID, image: ChartImage) -> None:
    path = chart_path(root, user_id, client_message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(image.data)
    temporary.replace(path)


def chart_exists(root: Path, user_id: UUID, client_message_id: UUID) -> bool:
    try:
        return chart_path(root, user_id, client_message_id).is_file()
    except ChartRejected:
        return False


def load_chart(root: Path, user_id: UUID, client_message_id: UUID) -> tuple[bytes, str] | None:
    try:
        path = chart_path(root, user_id, client_message_id)
    except ChartRejected:
        return None
    if not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) > MAX_CHART_BYTES:
        return None
    media = sniff_media(data)
    if media is None:
        return None
    return data, media
