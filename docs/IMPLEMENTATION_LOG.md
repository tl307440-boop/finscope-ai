# 实现日志

## 2026-10-15 T00 工程地基

### 本轮范围

初始化 `finscope-ai` 仓库，落下规范与设计文档，接通 PostgreSQL、API、Worker 和前端运行状态页。登录、行情、宏观、财报和 AI 未开始。

### 变更

- 根目录：`AGENTS.md`、`README.md`、`.env.example`、`docker-compose.yml`、`Makefile`、`.pre-commit-config.yaml`、CI。
- 文档：`docs/SPEC.md`、`docs/TASKS.md`、`docs/ADR-001-architecture.md`、`docs/API.md`。
- 后端：FastAPI `/api/v1/health/live` 与 `/api/v1/health/ready`，请求编号，结构化错误，日志脱敏，Alembic 修订 `20261015_0001`（pgvector 与 `worker_heartbeats`）。
- 前端：`/` 运行状态页，加载、就绪、缺失/停止和网络失败状态；`DATA_MODE=demo` 时显示“演示数据 / 非实时”。
- 本轮没有写入演示行情或用户。

### 验证

在 `backend` 目录：

- `uv run ruff check .` 通过
- `uv run ruff format --check .` 通过
- `uv run mypy app` 通过，32 个源文件无问题
- `uv run pytest` 通过。数据库未启动时 38 通过、1 个集成测试跳过；连接到本机 pgvector 后全部通过，不再跳过

在 `frontend` 目录：

- `npm test` 10 个测试通过
- `npm run typecheck` 通过
- Docker 构建中的 `vue-tsc --noEmit && vite build` 通过

本机 5432 和 8000 已被其他项目占用，因此使用：

```powershell
$env:FINSCOPE_DB_PORT = "54329"
$env:FINSCOPE_API_PORT = "18000"
$env:FINSCOPE_WEB_PORT = "8080"
docker compose up --build -d
```

实际结果：

- `db`、`api` 健康，`worker` 和 `frontend` 已启动
- `GET http://127.0.0.1:18000/api/v1/health/live` 返回 200，`data_mode` 为 `live`
- `GET http://127.0.0.1:18000/api/v1/health/ready` 返回 200，数据库与 Worker 均为 `up`
- `GET http://127.0.0.1:18000/docs` 返回 200
- `GET http://127.0.0.1:8080/` 返回 200，页面经 nginx 转发 `/api`
- Alembic 日志：`Running upgrade  -> 20261015_0001`
- 停止 Worker 后 ready 返回 503，`reason` 为 `heartbeat_stopped`，数据库仍为 `up`，响应中没有口令
- 浏览器打开 `http://127.0.0.1:8080/`：1440 宽为四列，390 宽为单列；显示“运行正常”“尚未接入”和北京时间
- 未知路由显示“页面不存在”，返回链接可用
- Worker 停止后页面显示“部分组件不可用 / 已停止”；重新启动并点击“重试”后恢复“运行正常”

### 已知局限

- T01 及之后的任务均为 `NOT_IMPLEMENTED`。
- 当前机器需改宿主机端口；仓库默认仍是 5432、8000、8080。
- FastAPI `TestClient` 在 Starlette 1.8 下提示改用 `httpx2`，测试仍通过。
- 容器为了本机卷权限以默认用户运行，不是生产加固。

### 下一项

T01：登录、退出、刷新 token、researcher/admin 权限和前端路由守卫。

## 2026-10-15 T01 登录与权限

### 本轮范围

账号登录、刷新、退出、个人偏好，以及管理员才能读取的数据源配置状态。没有公开注册，也没有行情数据。

### 变更

- 迁移 `20261015_0002`：`users`、`refresh_sessions`、`user_preferences`。
- 密码使用 Argon2id。刷新凭据只保存哈希，放在 HttpOnly cookie。访问令牌是短期 JWT。
- 未知账号、错误密码和停用账号返回同一句“账号或密码错误”。连续失败返回 429。
- 研究者调用 `GET /api/v1/admin/providers` 返回 403。响应只说明密钥是否已配置。
- 前端：`/login`、`/settings`、`/data-center`、`/forbidden`。未登录访问账号页会跳到登录页。

### 验证

- `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy app` 通过。
- `DATABASE_URL` 指向本机 pgvector 时，`uv run pytest` 通过，包含登录、刷新轮换、退出和 403。
- `npm test` 20 个测试通过，`npm run typecheck` 通过。

### 已知局限

- 登录失败计数在单个 API 进程内存中，多进程部署不会共享。
- 管理页目前只展示密钥是否配置，不执行同步。
- FastAPI `TestClient` 仍提示未来改用 `httpx2`。

### 下一项

T02：补齐首批业务表迁移，并保证空库可以一键升级。

## 2026-10-15 T02 首批业务表

### 本轮范围

把行情、交易日历、宏观序列和同步任务表放进迁移。不抓取真实行情，也不写入演示价格。

### 变更

- 迁移 `20261015_0003`：`data_providers`、`sync_jobs`、`sync_job_events`、`instruments`、`trading_calendars`、`market_bars`、`macro_series`、`macro_observations`。
- 价格使用 `NUMERIC(20,6)`，成交额和宏观数值使用 `NUMERIC(24,6)`。
- 宏观观测在 `realtime_start` 为空时，用唯一索引里的日期哨兵避免重复行；表中仍保存空值。
- `instruments.industry_id` 先不建外键，产业表还没有。

### 验证

- Ruff、Mypy 通过。
- `uv run pytest` 通过。其中空库测试会新建 `finscope_empty_t02`，执行 `alembic upgrade head`，核对上述表和价格精度，然后删除该库。

### 已知局限

- 这些表还没有采集接口。同步任务状态机留到 T07。
- 板块、快照和财务事实表不在本轮。

### 下一项

T03：统一数据源适配器，处理 429、503 和超时，并避免失败污染已有数据。

## 2026-10-15 T03 数据源适配器

### 本轮范围

统一上游请求的重试、限速和质量检查。不接入 FRED 或 AKShare，也不把样例价格当成真实行情。

### 变更

- `ResilientHttpClient` 只对 429、503 和超时做有限次重试，并限制 Retry-After。
- K 线质量规则 `bar-quality-v1`：价格或成交量为负、OHLC 不成立则拒绝入库；复权后偏离过大标记为待审。
- 待审记录不能覆盖已经合格的 K 线。上游失败时写入函数不会被调用。
- `DATA_MODE=live` 拒绝演示行情。演示样例带“演示数据/非实时”说明。
- 管理页可模拟限流、不可用和超时。接口为 `POST /api/v1/admin/provider-probes`。

### 验证

- Ruff、Mypy 通过。
- `uv run pytest` 通过，包含 429/503/超时、质量规则，以及失败后原 K 线收盘价不变。
- 前端 `npm test` 与 `npm run typecheck` 通过。

### 已知局限

- 真实 FRED、AKShare、SEC 适配器还没接。探测使用本地脚本，不访问外网。
- 限速间隔只在单个客户端实例内生效。

### 下一项

T04：导入首批证券和交易日历，搜索时返回唯一交易所代码。

## 2026-10-15 T04 证券与交易日历

### 本轮范围

导入指数、20 只 A 股和 8 只美股的元数据，以及 2024 年国庆窗口的沪深交易日历。不写入价格。

### 变更

- `600519.SH` 映射为 `SSE:600519`。`000001` 同时对应上证指数和平安银行，搜索结果不合并。
- 美股 CIK 补成 10 位。`320193` 对应 `NASDAQ:AAPL`。
- 交易日只查日历表。2024-10-01 是周二但休市，2024-09-29 是周日但开市。未导入的日期返回 unknown，不用星期几填。
- 运行状态页可以搜索代码或名称。

### 验证

- Ruff、Mypy 通过。
- `uv run pytest` 通过，包含唯一代码、重名代码、CIK 和日历边界。
- 前端测试与类型检查通过。

### 已知局限

- 日历只覆盖 2024 年 9 月 29 日到 10 月 13 日的沪深市场，不是完整历史。
- 美股 CIK 来自公开编号的静态清单，本轮没有请求 SEC。

### 下一项

T05：接入 FRED。有 Key 能同步，没 Key 显示不可用，并且 as_of 不能看到当时尚未公布的数据。




