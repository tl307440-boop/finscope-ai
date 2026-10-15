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
