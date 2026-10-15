# FinScope AI —— 本机运行说明

这套脚本把这台电脑上的 FinScope AI 完整跑起来（数据库 + 后端 API + 后台 worker + 前端界面），
**不需要 Docker**，双击就能用。

---

## 一、启动

双击本文件夹里的 **`start.bat`**。

脚本会自动依次做完 4 件事，全部完成大约 10 秒：

1. 启动数据库 PostgreSQL 16 + pgvector（端口 5432）
2. 执行数据库结构迁移（`alembic upgrade head`）
3. 启动后端 API（端口 8000）和后台 worker
4. 启动前端界面（端口 8080），最后自动打开浏览器

看到下面这段就说明成功了：

```
========================================
   FinScope AI 已就绪
========================================

   前端界面   http://127.0.0.1:8080
   接口文档   http://127.0.0.1:8000/docs
   健康检查   http://127.0.0.1:8000/api/v1/health/ready

   登录账号   admin
   登录密码   FsScope!2026
```

---

## 二、访问地址与登录账号

| 用途 | 地址 |
| --- | --- |
| 前端界面（日常使用） | <http://127.0.0.1:8080> |
| 接口文档（Swagger） | <http://127.0.0.1:8000/docs> |
| 健康检查 | <http://127.0.0.1:8000/api/v1/health/ready> |

**登录账号：`admin`　登录密码：`FsScope!2026`**

> 密码是首次初始化时写入数据库的。想改密码，见下面「五、修改管理员密码」。

---

## 三、停止 与 查看状态

| 操作 | 双击 |
| --- | --- |
| 停止全部服务（**数据会保留**） | `stop.bat` |
| 查看当前运行状态 + 就绪检查 | `status.bat` |

`stop.bat` 只是把进程停掉，数据库里的数据（用户、自选股、文档等）都留在
`C:\finscope-runtime\pgdata`，下次 `start.bat` 直接接着用。

---

## 四、命令行选项（可选）

如果你更习惯用 PowerShell：

```powershell
cd C:\Users\26845\Desktop\毕设tl\finscope-ai\windows-deploy

.\start.ps1                  # 生产模式（默认），前端 8080
.\start.ps1 -Mode dev        # 开发模式，前端 5173，改代码热更新
.\start.ps1 -Rebuild         # 先重新构建前端再启动
.\start.ps1 -NoBrowser       # 启动后不自动开浏览器
.\stop.ps1
.\status.ps1
```

---

## 五、修改管理员密码

密码只在**第一次**初始化时写入，之后改 `.env` 不会生效。要改密码，用这条命令
（把 `新密码` 换成你要的，至少 8 位）：

```powershell
cd C:\Users\26845\Desktop\毕设tl\finscope-ai\backend
$env:ADMIN_USERNAME='admin'; $env:ADMIN_EMAIL='admin@finscope.local'; $env:ADMIN_PASSWORD='新密码'
& .\.venv\Scripts\python.exe -m app.scripts.seed
```

---

## 六、日志

出问题时先看这里：

```
C:\finscope-runtime\logs\
├── postgres.log      数据库
├── api.out.log       后端 API 标准输出
├── api.err.log       后端 API 错误
├── worker.out.log    后台 worker
├── worker.err.log    后台 worker 错误
├── web.out.log       前端服务
└── web.err.log       前端服务错误
```

---

## 七、这套环境装了什么、装在哪

| 组件 | 版本 | 位置 |
| --- | --- | --- |
| Node.js | v24.21.0 | `D:\Program Files\nodejs` |
| npm / pnpm | 11.19.0 / 12.4.1 | 随 Node.js |
| uv（Python 包管理器） | 0.13.1 | `C:\Users\26845\.local\bin` |
| Python（后端运行时） | 3.12.15 | `backend\.venv` |
| PostgreSQL + pgvector | 16.2 + 0.6.2 | `C:\finscope-runtime\pgsql` |
| 数据库数据目录 | — | `C:\finscope-runtime\pgdata` |

### 为什么数据库不在项目文件夹里？

项目源码就在 `C:\Users\26845\Desktop\毕设tl\finscope-ai`，但**数据库运行时被刻意放在
`C:\finscope-runtime`**（另一处纯英文路径）。

原因：PostgreSQL 在 Windows 上按系统 GBK 编码处理文件路径，`毕设tl` 这样的中文路径会让
`initdb` 直接失败（`FATAL: invalid byte sequence for encoding "UTF8"`）。
放到纯英文路径是唯一稳妥的做法，源码放哪里不受影响。

项目里的 `backend\.env` 已经把 `DATABASE_URL` 指向 127.0.0.1:5432，两边是连通的。

---

## 八、常见问题

**Q：双击 `start.bat` 一闪就没了 / 提示 PowerShell 无法加载脚本？**
脚本已经带了 BOM，正常情况下不会。若仍被拦截，在 PowerShell 里执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Q：`status.bat` 里「就绪检查」返回 503？**
说明 worker 没起来（就绪检查要求数据库和 worker 心跳都正常）。看
`C:\finscope-runtime\logs\worker.err.log`。重新跑一次 `stop.bat` + `start.bat` 通常就好。

**Q：改了前端代码，界面没变？**
生产模式用的是已构建的产物。加 `-Rebuild` 重新构建：

```powershell
.\start.ps1 -Rebuild
```

或者直接用开发模式 `.\start.ps1 -Mode dev`（端口 5173，热更新）。

**Q：端口 8000 / 8080 / 5432 被占用？**
先 `stop.bat`。如果提示端口仍被占用，说明有别的东西占了，用
`netstat -ano | findstr :8000` 找到 PID 再处理。

**Q：想彻底重来（清空所有数据）？**
先 `stop.bat`，删掉 `C:\finscope-runtime\pgdata` 文件夹，再重新初始化
（需要 `initdb` + `createdb` + `alembic upgrade head` + `seed`）。
不建议随便执行，现有数据会全部丢失。

---

## 九、相关文件

- 部署脚本本体：`start.ps1` / `stop.ps1` / `status.ps1` / `_common.ps1`
- 双击入口：`start.bat` / `stop.bat` / `status.bat`
- 后端配置：`..\backend\.env`
- 项目原始说明：`..\README.md`
