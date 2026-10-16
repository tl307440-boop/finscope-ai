# FinScope AI

金融投研辅助系统，供本科毕业设计使用。它不提供交易执行，也不承诺收益。

当前完成的是 **T00 工程地基**：仓库规范、Docker Compose、健康检查、前端运行状态页和自动化测试。登录、行情、宏观、财报和 AI 还没有实现。

## 本地运行

需要 Docker、Python 3.12 和 Node.js 22。Windows 可直接使用下面的 PowerShell。

```powershell
cd backend
uv sync --extra dev
cd ../frontend
npm ci
cd ..
docker compose up --build -d
```

启动后：

- 前端：<http://localhost:8080>
- 接口文档：<http://localhost:8000/docs>
- 存活检查：<http://localhost:8000/api/v1/health/live>
- 就绪检查：<http://localhost:8000/api/v1/health/ready>

本机如果 5432 或 8000 已被占用，可以改宿主机端口，容器内部地址不变：

```powershell
$env:FINSCOPE_DB_PORT = "54329"
$env:FINSCOPE_API_PORT = "18000"
$env:FINSCOPE_WEB_PORT = "8080"
docker compose up --build -d
```

这时接口在 <http://localhost:18000>，页面仍在 <http://localhost:8080>。页面里的 `/api` 和 `/docs` 由前端容器转发，不依赖宿主机 8000 端口。

`/health/live` 只表示 API 进程还在。`/health/ready` 会检查数据库和 Worker 心跳。Worker 未写入心跳时返回 503，并说明原因，不会假装系统已经就绪。

开发时也可以分开启动：

```powershell
docker compose up -d db
cd backend
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run python -m app.workers.main
uv run python -m app.workers.scheduler
cd ../frontend
npm run dev
```

`app.workers.scheduler` 是任务调度器（规格书 §11）：它只按幂等标识往 `sync_jobs` 写 `queued` 记录，不执行任务；真正跑任务的是 `app.workers.main`，两个进程可以各自单独重启、Ctrl+C 都能干净退出。触发时刻与 `SCHEDULER_ENABLE_*` 开关在 `backend/.env` 里配置。`docker compose up` 会默认带上 `scheduler` 服务；不想定时抓取就 `SCHEDULER_ENABLED=false` 或停掉该服务。

前端开发服务器是 <http://localhost:5173>，`/api` 会代理到 8000 端口。

## 检查

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify.ps1
```

后端测试在没有 PostgreSQL 时会跳过迁移集成测试。数据库起来之后再跑一次 `uv run pytest`，会真正执行迁移、pgvector 扩展和心跳读写。

`DATA_MODE` 只有 `live` 和 `demo`。默认是 `live`。只有显式设为 `demo` 时，界面和响应才会标出“演示数据 / 非实时”。本阶段没有演示行情。

## 登录

登录页是 `/login`。未登录访问 `/settings` 会跳回登录页。研究者打开 `/data-center` 会看到无权限提示；管理接口对研究者返回 403。

创建管理员时在环境变量中设置 `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD`，密码至少 8 位，然后运行：

```powershell
cd backend
uv run python -m app.scripts.seed
```

命令输出不会打印密码。未设置这三个变量时，不会创建账号，也不会写入行情。

## 配置

复制 `.env.example` 为 `.env` 后再修改。示例口令只适合本机。生产环境拒绝示例 `JWT_SECRET` 和含 `change-me` 的数据库地址。

密钥、`.env`、用户上传和数据库备份不要提交。

## 文档

- `docs/SPEC.md`：完整详细设计规格书。
- `docs/TASKS.md`：T00 起的分阶段任务与验收清单。
- `docs/API.md`：接口说明（含 `/companies/{id}/financials` 与 `/simulation/*`）。
- `docs/UI-设计规范.md`：界面规范。栅格尺寸、颜色 token、组件清单、图表规则，
  以及十张效果图到路由的对照表。改页面前先看这一份。
- `docs/design-evidence/`：界面改版的截图取证（改前 / 每轮 / 改后）。
- `docs/IMPLEMENTATION_LOG.md`：每轮的变更、验证和已知局限。
- `docs/thesis/`：论文与答辩材料（测试与图表、结构图、ER 图、架构图、业务流程图、
  用例图、部署与演示）。
- `docs/screenshots/`：24 张运行截图，由 `frontend/e2e/screenshots.spec.ts` 生成。

## 数据来源

A 股日线按顺序尝试两个免费上游，第一个通了就用：

1. 东方财富 `push2his.eastmoney.com`（主源，带成交额）
2. 腾讯行情 `web.ifzq.gtimg.cn`（备用，**不返回成交额**）

腾讯不返回成交额时，`amount` 存 `NULL`、界面显示「—」，
不用 量×价 之类的办法补一个看起来合理的数字。

美国宏观走 FRED：配置了 `FRED_API_KEY` 用官方 API（带 ALFRED 历史版本）；
没配置时改用 FRED 官方公开 CSV（`fredgraph.csv`，免密钥），availability 标为 `public_csv`。
公开 CSV 只有最新修订值，没有历史版本，所以每次抓取都把可见起点记为抓取当天
（纽约时间 6 点前算前一天），历史时点回看时不会提前看到数据。
中国宏观只接受官方导出的 CSV 导入。

公告只收官方披露平台：巨潮资讯（A 股，`hisAnnouncement/query`）和 SEC EDGAR submissions（美股），
标题按关键词归类为业绩 / 分红 / 问询 / 定期报告 / 治理 / 其他，保留原文链接。

行业归属用申万一级行业（2021 版），只覆盖参考证券池里的 20 只 A 股，人工整理
（`backend/app/catalog/industry_reference.py`，来源标记 `sw2021-curated`）。

### 一键灌入真实数据

数据库为空时，在 API 容器里跑一遍引导脚本（只访问上面列出的公开上游）：

```powershell
docker compose exec api python -m app.scripts.bootstrap_data            # 全部
docker compose exec api python -m app.scripts.bootstrap_data --only fred,sec
docker compose exec api python -m app.scripts.bootstrap_data --only none # 只写证券、日历、行业目录
```

步骤：参考证券与行业目录 → 24 个 A 股标的日线（默认 2025-01-02 起，`--since` 可改）并由上证指数有成交的日子推出开市日 →
FRED 9 个序列近 6 年 → SEC companyfacts（8 家美股，FY2021 起的 10-K/10-Q 关键科目）→
巨潮 20 只 A 股 + SEC 8 家美股近 120 天公告。SEC 请求需要 `SEC_USER_AGENT`（带联系邮箱）。

## 尚未实现

- 美国宏观：没有 `FRED_API_KEY` 时只有公开 CSV 的最新修订值，没有 ALFRED 历史版本（BLOCKED：需要密钥）。
- 中国宏观：没有官方 CSV 入库，六个序列都是 `unavailable`。
- AI 段落：没有配置 `LLM_*`，财报解读走规则分支，不会生成模型文字。
- 推演沙盘（`/simulation/mvp`）：默认是确定性规则引擎，分析师 → 多空辩论 → 交易员 → 风控三方 → 组合经理，
  另有决策记忆和网格实验，不调用模型。
  - TradingAgents 原版图（`engine=tradingagents`）需要 `LLM_*`，没配置时接口返回 `LLM_NOT_CONFIGURED`，
    真实模型路径未验证（BLOCKED：需要密钥）。
  - 人在回路干预与 `/graph`、`/debates` 子路由未实现（NOT_IMPLEMENTED）。
- 行情涨跌幅、均线偏离与宏观同比 / 较上期仍由前端用接口返回的原始收盘价或观测值换算，
  与 `docs/UI-设计规范.md`「前端不算财务数字」不一致，待移到后端。
  财报类数字（同比、毛利率、负债率、规则读数、公司页区间涨跌幅）已全部由后端计算。
- A 股财报来自东方财富 F10 披露报表（非官方公开 API，累计口径，没有单季拆分）；解析失败会报错而不是写错数。
- 美股日线需要 `TIINGO_API_KEY`（Tiingo 官方免费档，注册即得）。没配置时页面标「不可用」；真实请求路径未联网验证（BLOCKED：需要密钥）。
- 美股只收 Nasdaq / NYSE；搜索全市场可用，首次登录访问某家公司会在后台抓取财报，约几十秒后自动刷新。
- 板块强弱：成分不少于 3 只才出分，目前只有银行、非银金融、食品饮料三个板块可算。
- 热点只按官方公告条数统计，没有媒体新闻与情绪分。
- 调度器里的 `compute_financial_metrics`、`compute_macro_regime`、
  `refresh_hotspot_events` 只登记了触发与幂等键，执行器未接，默认关闭。
- 报告没有 PDF 导出接口。
- 列表接口还没有分页参数。
- 端到端测试只在 Chromium 上跑。

这些在页面上一律显示为 `unavailable` / `insufficient` / 空态并写明原因，
不会用假数据顶上去。
