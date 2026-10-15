from dataclasses import dataclass


@dataclass(frozen=True)
class DigestItem:
    title: str
    summary: str


def build_digest(report_titles: list[str], announcement_count: int) -> tuple[DigestItem, ...]:
    items: list[DigestItem] = []
    if report_titles:
        items.append(
            DigestItem(
                title="已保存的研究记录",
                summary="、".join(report_titles[:5]),
            )
        )
    else:
        items.append(DigestItem(title="已保存的研究记录", summary="今天没有新的研究记录"))
    if announcement_count > 0:
        items.append(DigestItem(title="合规公告", summary=f"今天收录 {announcement_count} 条标题"))
    else:
        items.append(DigestItem(title="合规公告", summary="今天没有新的合规公告"))
    items.append(DigestItem(title="通知", summary="只在站内列出，不会向外发送邮件或推送"))
    return tuple(items)
