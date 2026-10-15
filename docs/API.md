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

## 证券搜索

`GET /api/v1/instruments/search?q=600519.SH` 返回唯一 `canonical_code`：`SSE:600519`。`000001` 会同时返回 `SSE:000001`（上证指数）和 `SZSE:000001`（平安银行），`unique` 为 false。没有匹配时 `empty_message` 为“暂无该证券”。

`GET /api/v1/market/calendar?exchange=SSE&session_date=2024-10-01` 按已导入日历返回 `closed`。未导入的日期是 `unknown`，`weekday_inferred` 始终为 false。

运行 `uv run python -m app.scripts.seed` 会写入这批元数据和交易日历，不写入行情价格。

## 宏观序列

`GET /api/v1/macro/series` 返回已核对的美国序列定义。没有 `FRED_API_KEY` 时 `availability` 为 `unavailable`。响应里没有观测值。

`GET /api/v1/macro/series/UNRATE/observations?as_of=2020-02-08` 只返回该纽约日历日已经可见的修订。`as_of` 可以是 `YYYY-MM-DD`，也可以是带时区的时间。发布日当天不可见。没有数据时 `empty_message` 为“该日期还看不到已公布的数值”。

`GET /api/v1/macro/assessments?as_of=1776-07-04` 用截至该日已公布的 GDP、CPI 和联邦基金利率做规则分。数据不足时 `regime_code` 为 `insufficient_data`，不编造分数。增长分只看 GDP，不把就业人数水平加进去。说明里写明这不是经济定论。

管理员 `POST /api/v1/admin/fred-syncs`，正文包含 `series_code`、`observation_start`、`observation_end`。没密钥时 HTTP 503，`error.code` 为 `DATA_UNAVAILABLE`，`rows_written` 为 0。密钥不会出现在响应里。

## A 股日线

当前只同步三只：`600519.SH` 贵州茅台、`601318.SH` 中国平安、`000858.SZ` 五粮液。

`GET /api/v1/market/bars/{instrumentId}?start=1999-01-04&end=1999-01-05&adjustment=none&as_of=1999-01-04` 返回不复权日线。`as_of` 按上海日历日过滤，更晚的 K 线不会出现。没有同步时 `empty_message` 说明不会用演示价格填补。成交量单位是手，和 AKShare 日线一致。每根 K 线附带 `ma5`、`ma10`、`ma20`、`ma60` 和 `simple_return`。样本不足时这些字段不出现。缺失的交易日不会补成 0。`adjustment` 可以是 `none`、`qfq`、`hfq`。

## SEC 样例

`GET /api/v1/sec/facts?cik=320193&as_of=2024-03-01` 解析仓库里的 companyfacts 样例。`notice` 写明不是本次从 SEC 拉取。同一期间如果后来有修订，观察日只看到当时已申报的那一版。美元和股份不会混在同一个单位里。来源地址、accession、form、fy、fp 和 filed 会一起返回。重复解析同一份内容不会把事实再写一遍。没有 User-Agent 时不会请求 SEC。

## 财务试算

`POST /api/v1/metrics/evaluations` 接收累计数。利润表和现金流按 Q1=累计、Q2=二季累计减一季累计、Q3=三季累计减二季累计、Q4=年报减三季累计。资产负债表只保留时点值。口径或版本不一致时不减。TTM 是四个单季相加。利润小于等于 0 时市盈率是 N/A，不会给出负数。响应里的 `notice` 写明这是计算样例，不是公司真实财报。

## 公司研究

`GET /api/v1/companies/{instrumentId}/research?as_of=2024-06-01&period_a=2022-12-31&period_b=2023-12-31` 对比两个报告期。每个数字带 `accession_no`、`filed` 和 `source_url`。两个报告期相同会返回 422。没有现金流、毛利率或负债数据时，规则状态是 `not_evaluated`，不会被写成没有风险。样例收入不是苹果公司的真实报表。

## 上传 PDF

`POST /api/v1/documents` 需要登录。内容不是 `%PDF-` 时返回 422，`REJECTED_FORMAT`。超过 `UPLOAD_MAX_MB` 或 200 页分别是 `REJECTED_SIZE`、`REJECTED_PAGES`，文件不落盘。同一用户相同 SHA256 返回 `duplicate`，不另存一份。扫描件状态是 `ocr_required`。损坏文件记录 `failed`，不保存路径。文字 PDF 状态是 `queued`。保存路径是 `data/uploads/<用户>/<sha256>.pdf`，不用原始文件名。

`POST /api/v1/documents/{documentId}/index` 只处理当前用户名下、状态为等待解析的文字版 PDF。扫描件返回 422，不会写入向量。解析完成后可用 `GET /api/v1/documents/search?q=` 检索。结果包含 `document_id`、`chunk_id`、`page_start` 和 `page_end`。向量维度固定为 1536，模型名是 `local-hashing-v1`。没有配置外部向量接口时不会假装调用远程模型。早于 `as_of` 上传的文件才会出现；别人的文件不会出现。

## 模型状态

`GET /api/v1/analysis/model-status` 只说明模型是否已配置，不返回密钥或接口地址。未配置时不会生成 AI 段落。模型输出要经过 JSON 结构和引用校验；不存在的引用、材料里没有的数字，以及连续两次无效 JSON，都会得到 `analysis_validation_failed`，不会被当成已核实。超时不会补写分析。

`POST /api/v1/analysis/filing` 需要登录。它读取截止日之前已经入库的样例事实，计算两个报告期的变化，并检索该用户在截止日之前上传的文档。没有模型时只保存这些数字和申报号。模型输出未通过校验时，正文仍只保留已入库数字，状态为 `failed`。`GET /api/v1/analysis/runs/{runId}` 只返回当前用户自己的记录。

## 问答

`POST /api/v1/assistant/sessions` 新建会话。`POST /api/v1/assistant/sessions/{sessionId}/messages` 用 `client_message_id` 保存回答，并以 SSE 分片返回。同一个 `client_message_id` 再次发送时直接重放已保存的文本，不再生成一次。未配置模型时回答固定说明不会编造财务数字。已经完成的回答在取消后仍然保留。其他用户不能读取会话。

## 资料库

`GET /api/v1/analysis/reports?q=` 按标题筛选当前用户的研究记录，按创建时间从新到旧排列。`DELETE /api/v1/analysis/reports/{reportId}` 只隐藏自己的记录。`GET/POST /api/v1/watchlists` 管理自选分组，加入和移出证券也只作用于自己的分组。其他用户看到的列表是空的，删除别人的记录或自选会得到 404。

管理员 `POST /api/v1/admin/akshare-syncs`。同一交易日的重复行只保留第一条。上游失败时 `rows_written` 为 0，已有 K 线不改。自动测试使用脚本响应，不访问外网。

## 同步任务

`POST /api/v1/admin/sync-jobs` 只负责入队。同一 `dedupe_key` 在排队或执行中时，再次提交返回原来的任务，`created` 为 false。Worker 用 `FOR UPDATE SKIP LOCKED` 每次只领一条。超时或上游失败会按次数重试；校验错误和未配置密钥不重试。失败记录保留，`POST /api/v1/admin/sync-jobs/{id}/retries` 另开一条。`GET /api/v1/admin/providers` 带上 `last_health_status`。

没有公开注册。管理员可由 `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD` 配合 `python -m app.scripts.seed` 创建；未设置这些变量时不创建账号。
