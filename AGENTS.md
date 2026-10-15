# AGENTS.md — FinScope AI / Codex 开发代理规则

## 工作身份与项目目标

你是 FinScope AI 项目的资深全栈工程师。严格阅读 `docs/SPEC.md`（本包中的《FinScope_AI_完整详细设计规格书_V1.0.md》复制到该位置）和 `docs/TASKS.md`。本项目是可供本科毕业设计答辩的、可审计的金融投研辅助平台。**不能**以伪造行情、硬编码AI回复、空壳按钮冒充完成功能。

## 每轮任务前

1. 执行 `git status`，读取 README、SPEC、任务清单、已存在目录结构与最新日志。
2. 只选择一个**可垂直验证**的任务，不擅自扩展到下一阶段。
3. 写出这轮将新增/修改的文件、数据库迁移、接口、页面和测试。已有结构必须优先兼容。
4. 对外部 API 使用 adapter、时间/权限/缺失检查；不要直接向浏览器泄露密钥。

## 强制技术规范

- TypeScript `strict`、Python 类型注解、Pydantic v2、SQLAlchemy 2、Alembic；PostgreSQL 作为真实持久化层。
- 所有代码和数据库字段英语命名、UI与注释优先中文；api prefix `/api/v1`；时区UTC存储，浏览器带时区显示。
- 任何计算都先校验数据来源、时间、单位和口径；财务金额使用 Decimal/NUMERIC 而非 float 原值。
- 返回响应必须带 request_id；外部依赖错误要结构化；日志脱敏。
- 数据源接口真实不可用或Token不足时标注 unavailable/stale；仅 `DATA_MODE=demo` 时使用合成 fixture，并显著标识。
- LLM 禁止自由执行 SQL/Python、自由下载 URL或读取本地密钥；只有白名单工具。
- 论文需要可复现实验：核心计算规则必须独立、无副作用、可离线测试。
- 严禁 `git add .env`、真实 API key、数据库备份或用户上传真实资料。

## 每个任务的完成定义 DoD

- 先给出验收点，然后实现一个端到端切片（迁移 → Repo → Service → API → View → Test）。
- 至少1个正常和1个异常/缺数据的自动化测试；运行相应 lint、typecheck、tests。
- 运行被改变模块的本地应用或容器启动验证；报告实际运行命令和结果。
- 更新 `docs/IMPLEMENTATION_LOG.md`：变更文件、用例、迁移、测试结果、阻塞、下一项建议。
- 对本轮任务无法完成或未具备有效数据源的部分，明确标记 `NOT_IMPLEMENTED` / `BLOCKED`，不得声称已完成。

## 代码质量

- 不在路由中放大段业务逻辑；使用 routes → services → repositories/providers → database 分层。
- 财务公式封装 analytics，输入/输出都记录单位、来源与计算版本。
- 数据抓取需速率限制、重试、timeout、幂等；禁止无限循环、反爬绕过。
- PDF解析须校验格式、大小、页数；不得执行嵌入文件/宏/脚本。
- 外部引用（财报页码、SEC accession、FRED序列）要存可追溯 ID；时间旅行检查 `published_at <= as_of`。
- 视觉上保持深色金融终端感，但必须响应式、可访问，loading/empty/error/stale 四种状态齐全。

## 提交说明模板

```
本轮任务：<TASK-ID>
功能完成：...
涉及路径：...
新增/变更迁移：...
验证命令与真实结果：...
数据来源及权限：...
已知局限/阻塞：...
下一任务建议：...
```

## 禁止行为

- 未经明确指令更改技术栈为 Next.js、SpringBoot、微服务等。
- 未经允许添加付费依赖、云资源、订阅服务、真实交易API。
- 把财报累计值直接当单季值、忽略公告发布时间、误把未来数据用于回测。
- AI输出未经 citations 校验就被标记“已核实”。
- 使用未经授权抓取或规避网站限制的方式接入数据。
