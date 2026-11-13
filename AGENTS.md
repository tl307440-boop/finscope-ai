# AGENTS.md — FinScope AI 开发协作指南

## 项目定位

FinScope AI 是面向本科毕业设计与投研复盘的可审计金融投研辅助平台：
- **后端**：Python 3.12 + FastAPI + SQLAlchemy 2 + Pydantic v2 + PostgreSQL（pgvector）+ Alembic
- **前端**：Vue 3 + TypeScript（`strict`）+ Vite + Pinia + Element Plus + ECharts

## 工作方式（保持灵活）

- **按任务目标自主推进**：无论是端到端新特性，还是局部 Bug 修复、重构、性能优化或测试补全，均按实际需求拆解推进，不再强制每次只能改一个“迁移 → Repo → Service → API → View”垂直切片，也不强制动手前先输出固定格式清单。
- **按需查阅文档**：避免每轮通读冗长的早期规格文档，按修改范围定向查阅即可：
  - 总体设计与早期任务：`docs/SPEC.md`、`docs/TASKS.md`
  - 接口契约：`docs/API.md`
  - 界面规范：`docs/UI-设计规范.md`（改前端页面前必看，保持统一风格）
  - 演进记录：`docs/IMPLEMENTATION_LOG.md`（了解近期上下文时看末尾最近几节即可）
- **优先兼容与复用**：先了解受影响模块的现有抽象、工具与测试模式，优先兼容和复用已有实现。

## 核心红线（必须守住）

1. **数据真实与可审计**
   - 不伪造行情、财务指标或硬编码 AI 结论充当已完成功能；数据源不可用或数据缺口时如实返回 `unavailable` / `stale` 或清晰原因。
   - 仅在 `DATA_MODE=demo` 时允许使用合成样例数据，且需显式标识。
2. **投研严谨与防未来函数**
   - 时序与回测逻辑严格遵守数据可见性（如 `published_at <= as_of`），杜绝未来数据泄露。
   - 不把财报累计值直接当单季值；涉及财务金额的核心计算优先使用 `Decimal` / `NUMERIC`。
   - AI 助手输出的事实结论、数字与出处须经过核对链路（`Grounder` / `citations`）校验，未通过核对的内容不得标为“已核实”。
3. **数据源密钥与敏感信息安全**
   - 所有第三方取数密钥、会话 Cookie、大模型与搜索 Key（如扶摇、FRED、大模型、搜索、韭研等）**统一在网页「数据管理 → 密钥与数据源配置」填写加密存库**，不写死在服务端 `.env` 中。
   - 新增或接入密钥时的固定约定：① 在 `backend/app/services/runtime_settings.py` 的 `MANAGED` 注册 `ManagedKey`；② 在 `backend/app/core/config.py` 加同名配置项（默认值留空）；③ 业务取数通过 `effective_settings(engine)` 读取。若需测试连接或页面配置，复用 `backend/app/services/integration_probe.py` 与 `frontend/src/components/ui/IntegrationsPanel.vue`。
   - **严禁**提交 `.env`、真实 API 密钥、数据库备份或用户上传真实文件；密钥只放请求头或请求体，不进 URL、报错文案、日志或文档（测试只用明显假值）。
4. **AI 与数据抓取安全边界**
   - AI 助手工具调用与联网检索/抓取必须走白名单工具与适配器边界，禁止让模型自由拼接执行任意 SQL/系统命令或读取本地敏感配置。
5. **UI 风格与设计体系一致（前端硬约束）**
   - 保持现有「夜间交易台上的精密仪器」视觉风格与深浅双主题体系，以 `docs/UI-设计规范.md` 为准，**不另起一套配色、栅格或页面外壳**。
   - 颜色、字号与布局统一使用 `frontend/src/styles/tokens.css` 中的 CSS 变量和 `frontend/src/styles/global.css` 的标准布局类（`.stack`、`.grid-*`、`.tabular`），避免硬编码脱离主题的颜色。
   - 优先复用 `frontend/src/components/ui/` 既有组件（`PageHeader`、`PanelCard`、`StatCard`、`MetricTile`、`DeltaValue`、`StatusPill`、`SectionTabs`、`StateBlock`、`SourceFooter`、`IntegrationsPanel` 等）、Element Plus（`<el-table>`）和全局 `finscope` 主题的 `<VChart>`（`frontend/src/charts/index.ts`），不重复造同质化外壳组件。
   - 金融展示保持一致：A 股红涨绿跌、涨跌幅带正负号、数字等宽对齐、中文友好文案（不向终端用户暴露原始英文枚举或 API 路径）；完善 `loading / empty / error / stale` 状态与桌面/移动端响应式。
6. **不擅自变更既定技术底座**
   - 未经明确要求，不更换核心框架与技术栈（如改用 Next.js、SpringBoot 或拆分微服务），不擅自引入付费依赖、订阅服务或实盘交易下单 API。

## 工程实践建议

- **架构与编码习惯**：
  - 后端常规业务推荐保持 `routes -> services -> repositories / providers -> database` 分层，金融公式与可复现计算抽离到独立服务/分析模块便于离线单测；接口前缀 `/api/v1`，时间按 UTC 存储、前端按本地时区展示。
  - 代码、数据库字段英语命名；UI 文案与必要注释优先中文；外部抓取注意超时、重试、速率限制与幂等；文档解析校验文件格式、大小与页数；前端复用 `frontend/src/api/*` 与 Pinia store，不另造 HTTP 封装。
- **测试与验证（按改动范围按需运行）**：
  - 建议采用 TDD 或在修复 Bug / 新增功能时同步补充自动化测试（如 1 组正常路径 + 1 组异常/缺数据边界），不必对纯文案或微调强行套固定测试形式。
  - 提交前请尽量运行受影响端的检查，并在回复中**如实报告**实际运行过的命令、范围与已知局限/未测部分（无需每次小改都重启全套 Docker 容器）：
    - 一键全检：`powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`
    - 后端检查：`cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest tests/unit`（连库环境可跑全量 `uv run pytest`）
    - 前端检查：`cd frontend && npm test && npm run typecheck`
- **Git 提交与变更日志**：
  - Commit 信息采用清晰简练的 **Conventional Commits**（如 `feat(assistant): ...`、`fix(market): ...`、`refactor(...): ...`），说明改了什么及原因即可，不再强制套用多行固定模板。
  - 重要功能迭代、架构调整或关键 Bug 修复可在 `docs/IMPLEMENTATION_LOG.md` 末尾简要追加记录；日常小修、样式微调或纯重构可按需省略。
