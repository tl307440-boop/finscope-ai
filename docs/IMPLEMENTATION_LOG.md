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


