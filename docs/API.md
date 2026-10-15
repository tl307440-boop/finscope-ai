# API 说明（T00）

统一前缀 `/api/v1`。除下列接口外，规格书中的业务接口均为 `NOT_IMPLEMENTED`。

## GET /api/v1/health/live

进程存活。不访问数据库。

```json
{
  "data": {"status": "alive", "service": "api"},
  "meta": {
    "request_id": "uuid",
    "as_of": "2026-10-15T00:00:00+00:00",
    "source": "internal",
    "stale": false,
    "data_mode": "live"
  }
}
```

`DATA_MODE=demo` 时 `meta.notice` 会写明“演示数据/非实时”。

## GET /api/v1/health/ready

数据库和 Worker 都可用时返回 200。任一不可用时返回 503：

```json
{
  "error": {
    "code": "SERVICE_UNAVAILABLE",
    "message": "服务尚未就绪",
    "details": {
      "checks": {
        "database": {"status": "down", "reason": "database_unavailable"},
        "worker": {"status": "down", "reason": "heartbeat_missing"}
      }
    }
  },
  "request_id": "uuid"
}
```

响应不包含数据库口令、令牌或进程号。请求头 `X-Request-ID` 只接受 8 到 64 位字母、数字和连字符，否则由服务端重新生成。

## 认证

| 方法 | 地址 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/login` | `{username,password}`。成功返回 access token，刷新凭据放在 HttpOnly cookie `finscope_refresh`。失败统一为“账号或密码错误”。 |
| POST | `/api/v1/auth/refresh` | 用刷新 cookie 轮换会话并签发新的 access token。 |
| POST | `/api/v1/auth/logout` | 撤销刷新会话，返回 204。 |
| GET | `/api/v1/me` | 当前用户和偏好。 |
| PATCH | `/api/v1/me/preferences` | 更新主题、时区、涨跌颜色、默认市场和回看区间。 |
| GET | `/api/v1/admin/providers` | 仅管理员。只返回密钥是否已配置，不返回密钥本身。 |

未登录访问受保护接口返回 401。研究者访问管理接口返回 403。连续失败会返回 429。密码、刷新令牌和密钥不会写入日志或响应。

## 数据源探测

`POST /api/v1/admin/provider-probes`，仅管理员。正文 `{"scenario":"status_429"|"status_503"|"timeout"}`。这是本地模拟，不会访问外网，也不会改写已有 K 线。失败响应里 `details.preserved_existing` 为 true，`rows_written` 为 0。

`DATA_MODE=live` 时不会用演示价格填补失败。只有 `DATA_MODE=demo` 才允许读取带“演示数据/非实时”标记的合成样例。

没有公开注册。管理员可由 `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD` 配合 `python -m app.scripts.seed` 创建；未设置这些变量时不创建账号。
