"""技能：按问题按需展开的做法说明和工具（渐进式披露）。纯函数，不碰数据库和网络。

回合开始时模型只看到核心工具和 `load_skill`；`load_skill` 的说明里每个技能只有一句话的用途。
模型判断用得上某个技能时调用 `load_skill`，拿到这类问题的做法说明，技能带的工具从下一次
模型调用起出现在工具列表里。简单问题不用读一大堆用不上的规则和工具说明，复杂问题按需展开。

技能写在 `app/prompts/skills/*.md`：`---` 包起来的头（name / title / summary / tools 四行
`key: value`），后面是正文。改做法说明不用改代码。
"""

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOAD_SKILL = "load_skill"
MAX_SKILLS_PER_CALL = 3
# 回合开始就给的工具：几乎每类问题都用得到，单独做成技能反而多一轮。
CORE_TOOLS = ("calc", "financial_metrics", "instrument_search", "macro_series", "price_bars")
SKILLS_DIR = Path(__file__).resolve().parents[2] / "prompts" / "skills"
_HEAD_KEYS = frozenset({"name", "title", "summary", "tools"})


class SkillFileError(ValueError):
    """技能文件写错了。启动时就报出来，不要等到线上回合里才发现。"""


@dataclass(frozen=True)
class Skill:
    name: str
    title: str
    summary: str
    tools: tuple[str, ...]
    guide: str


def parse_skill(text: str, origin: str = "skill") -> Skill:
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillFileError(f"{origin}：第一行必须是 ---")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise SkillFileError(f"{origin}：头部没有用 --- 结束") from exc
    head: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or key.strip() not in _HEAD_KEYS:
            raise SkillFileError(f"{origin}：头部这一行看不懂：{line.strip()[:40]}")
        head[key.strip()] = value.strip()
    missing = sorted({"name", "title", "summary"} - head.keys())
    if missing:
        raise SkillFileError(f"{origin}：头部缺 {'、'.join(missing)}")
    guide = "\n".join(lines[end + 1 :]).strip()
    if not guide:
        raise SkillFileError(f"{origin}：没有正文")
    tools = tuple(item.strip() for item in head.get("tools", "").split(",") if item.strip())
    return Skill(head["name"], head["title"], head["summary"], tools, guide)


def read_skills(folder: Path = SKILLS_DIR) -> tuple[Skill, ...]:
    skills = tuple(
        parse_skill(path.read_text(encoding="utf-8"), path.name)
        for path in sorted(folder.glob("*.md"))
    )
    names = [skill.name for skill in skills]
    if len(names) != len(set(names)):
        raise SkillFileError("技能名重复")
    return skills


class SkillBook:
    """一个回合能用的技能：只留下工具在注册表里的部分；一个工具都没有的技能整个不出现。

    只有做法说明、本身不带工具的技能（如情景推演）照常出现。
    """

    def __init__(self, skills: Sequence[Skill], available: Iterable[str]) -> None:
        known = set(available)
        self.core = tuple(name for name in CORE_TOOLS if name in known)
        kept: list[Skill] = []
        for skill in skills:
            tools = tuple(name for name in skill.tools if name in known)
            if skill.tools and not tools:
                continue
            kept.append(Skill(skill.name, skill.title, skill.summary, tools, skill.guide))
        self._skills = {skill.name: skill for skill in kept}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def definition(self) -> dict[str, Any]:
        """`load_skill` 的工具定义。内容只随技能文件变，同一进程里每次逐字节相同（前缀缓存）。"""

        catalog = "；".join(f"{skill.name}（{skill.title}）：{skill.summary}" for skill in self)
        return {
            "type": "function",
            "function": {
                "name": LOAD_SKILL,
                "description": (
                    "加载处理某类问题的技能：返回这类问题的做法，并开放它带的工具。"
                    "只加载和当前问题有关的，可以一次加载几个。可用技能——" + catalog
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "names": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(self.names)},
                            "minItems": 1,
                            "maxItems": MAX_SKILLS_PER_CALL,
                            "description": "要加载的技能名",
                        }
                    },
                    "required": ["names"],
                },
            },
        }

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())


@dataclass(frozen=True)
class Loaded:
    """一次 `load_skill` 的结果：给模型的文字、加载的技能、开放的工具、是否全部成功。"""

    text: str
    names: tuple[str, ...]
    titles: tuple[str, ...]
    tools: tuple[str, ...]
    ok: bool


def load(book: SkillBook, raw_arguments: str, already: Iterable[str] = ()) -> Loaded:
    names = _names(raw_arguments)
    if names is None:
        return Loaded(
            f'参数不合法：要写成 {{"names": ["技能名"]}}，可用技能：{"、".join(book.names)}',
            (),
            (),
            (),
            False,
        )
    loaded = set(already)
    parts: list[str] = []
    found: list[str] = []
    tools: list[str] = []
    titles: list[str] = []
    ok = True
    for name in names[:MAX_SKILLS_PER_CALL]:
        skill = book.get(name)
        if skill is None:
            ok = False
            parts.append(f"没有这个技能：{name[:30]}。可用技能：{'、'.join(book.names)}")
            continue
        found.append(skill.name)
        titles.append(skill.title)
        tools.extend(skill.tools)
        if name in loaded:
            parts.append(f'<技能 name="{skill.name}">本回合已经加载过，按前面的做法继续。</技能>')
            continue
        loaded.add(name)
        opened = "、".join(skill.tools) if skill.tools else "（不带新工具，用已有工具）"
        parts.append(
            f'<技能 name="{skill.name}" 标题="{skill.title}">\n{skill.guide}\n'
            f"现在可以用的工具：{opened}\n</技能>"
        )
    return Loaded(
        "\n".join(parts),
        tuple(found),
        tuple(titles),
        tuple(dict.fromkeys(tools)),
        ok and bool(found),
    )


def _names(raw: str) -> list[str] | None:
    try:
        data = json.loads(raw.strip() or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("names", data.get("name"))
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return list(dict.fromkeys(item.strip() for item in value))
