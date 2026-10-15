# ADR-001：T00 工程地基

日期：2026-10-15

## 决策

- 采用规格书中的模块化单体：FastAPI、Vue 3、PostgreSQL（pgvector 镜像）和一个独立 Worker 进程。不引入 Redis、Next.js 或微服务。
- API 前缀为 `/api/v1`。成功响应使用 `data` + `meta`，错误响应使用 `error` + `request_id`。
- 存活和就绪分开。就绪同时检查数据库 `SELECT 1` 和 `worker_heartbeats` 表中的心跳。心跳过期、缺失或进程已停止都返回 503。
- T00 只增加 pgvector 扩展和 `worker_heartbeats`。用户、证券、行情等业务表留到 T02，避免空表假装功能完成。
- Worker 把心跳写入数据库，API 和 Worker 不再共享文件卷。数据库不可用时心跳写失败会重试，不会把异常里的连接串打进响应。
- 财务业务计算、登录和外部行情适配器不在本轮实现。

## 后果

后续迁移从修订 `20261015_0001` 继续。T01 做认证，T02 做首批业务表。
