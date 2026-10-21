"""robots.txt 解析与匹配（RFC 9309），纯函数，不碰网络。

标准库的 `urllib.robotparser` 不认 `*` 和 `$` 通配符，遇到 `Disallow: /*.pdf$` 会当成字面量，
结果是该拦的没拦住。这里按 RFC 自己实现：

- 先找点名我们产品标识的分组，没有再用 `*` 分组，都没有就都允许；
- 同一分组里用路径最长的规则；一样长时 Allow 优先；没有规则命中就允许；
- `Disallow:` 后面为空表示不限制；`*` 匹配任意字符，结尾的 `$` 表示路径到此为止。
"""

import re
from dataclasses import dataclass, field

MAX_ROBOTS_BYTES = 512 * 1024


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (允许, 路径模式)


class RobotsRules:
    def __init__(self, groups: list[_Group]) -> None:
        self._groups = groups

    @classmethod
    def parse(cls, text: str) -> "RobotsRules":
        groups: list[_Group] = []
        current: _Group | None = None
        collecting_agents = False
        # 有的站点（如中国政府网）文件以 BOM 开头，不去掉的话第一行 User-agent 认不出来，
        # 整份规则失效。
        for raw in text[:MAX_ROBOTS_BYTES].removeprefix("\ufeff").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            field_name, value = name.strip().lower(), value.strip()
            if field_name == "user-agent":
                if current is None or not collecting_agents:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                collecting_agents = True
                continue
            collecting_agents = False
            if current is None or field_name not in {"allow", "disallow"}:
                continue
            if value:  # 空的 Disallow 等于没写
                current.rules.append((field_name == "allow", value))
        return cls(groups)

    def allows(self, agent: str, path: str) -> bool:
        """`path` 是地址里 `/` 开头的部分，带查询串。"""

        rules = self._rules_for(agent.lower())
        best_length = -1
        allowed = True
        for permit, pattern in rules:
            if not _matches(pattern, path):
                continue
            length = len(pattern)
            if length > best_length or (length == best_length and permit):
                best_length, allowed = length, permit
        return allowed

    def _rules_for(self, agent: str) -> list[tuple[bool, str]]:
        named = [group for group in self._groups if agent in group.agents]
        if not named:
            named = [group for group in self._groups if "*" in group.agents]
        return [rule for group in named for rule in group.rules]


def _matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = ".*".join(re.escape(part) for part in body.split("*"))
    return re.match(expression + ("$" if anchored else ""), path) is not None
