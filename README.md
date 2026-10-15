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
cd ../frontend
npm run dev
```

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

## 尚未实现

T01 起的登录、权限、证券主数据、行情、宏观、财报和 AI 都还没有做。运行状态页里的“业务数据”会保持“尚未接入”。
