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

`GET /api/v1/macro/observations?codes=UNRATE,ISM_MFG&as_of=2026-10-17` 一次取回多条序列在该观察日可见的观测，`data = { pit_date, items[] }`，`items` 顺序与请求一致，每项结构同单序列接口。`codes` 1–20 个，不认识的代码返回 422 且 `error.details.unknown_codes` 列出；limit 由频率决定（日度 1000、周度 260、月 / 季度 120）。

美国宏观目录共 19 条：FRED 17 条 + 东方财富数据中心 `RPT_ECONOMICVALUE_USA` 转载的 ISM 制造业（`ISM_MFG` / `EMG00002790`）与服务业（`ISM_SVC` / `EMG00002791`）PMI。目录项带 `provider`（`fred` / `eastmoney`）与 `source_key`（东方财富指标 ID）。ISM 以发布日为 `realtime_start`，与 FRED 同样次日可见；来源只给最新值，首次同步前的历史为最新修订值，之后每次同步发现变化即新增修订版本：旧值截止到抓取日（纽约日历日）当天，新值从抓取日次日起可见。东方财富返回空列表或没有一行可解析时同步报 `UPSTREAM_PAYLOAD`，已入库数据不变；同一月份多行只留发布日最晚的一行。同步仍用 `fred_series` 任务，按序列的数据源分派。

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

两期变化率由后端算，规则是：同一概念、同一单位，并且期间长度相差不超过 20 天（单季对单季、全年对全年，时点对时点）。同一期末既有单季又有累计时优先单季。期间长度不同的两期返回 `change_reason=incomparable_period`，不给数。基数不为正时沿用 `year_over_year` 的口径，返回 `sign_change` / `loss_widened` / `prior_not_positive`，不给百分比。

`GET /api/v1/companies/{instrumentId}/financials?as_of=&reference=&compare=` 是公司页和 AI 财报解读页用的财务总览，页面不再自己算同比和比率。

- `as_of` 之后申报的事实不参与计算。库里有 EDGAR 真实事实时只用真实事实，否则用随包样例，`data_status` 是 `edgar` / `sample` / `empty`。
- `reference` 不填时取最新一个期末。`compare` 不填时取上一年同期（容差 14 天），`delta_label` 是「同比」；手动指定时是「较 yyyy-mm-dd」。
- `tiles` 依次是营业收入、净利润、经营现金流、毛利率、稀释 EPS、资产负债率。每个读数都带 `formula` 和来源（form、filed、accession_no、source_url、期间）。
- `trend` 和 `table` 分单季 / 全年两种口径。单季口径下，流量指标只比单季，不和累计数混比。
- `cash_vs_profit` 是近五个财年的经营现金流、净利润和倍数。净利润不为正时 `multiple` 为空。比率类读数（毛利率、负债率、流动比率）在分母不为正时同样不出数。
- `rule_observations` 是 R001–R006 的读数文字，只陈述数字，判定仍以 `/research` 的规则引擎为准。
- 没有任何可见事实时返回 200、`data_status=empty`，并在 `notice` 说明原因。证券不存在时返回 404。

`GET /api/v1/market/bars/{id}` 的 `window_returns` 是近 22 / 66 / 130 / 250 个交易日的区间收益率（最后一根收盘 ÷ 倒数第 N+1 根收盘 − 1）。日线不足 N+1 根时没有该键，不用更短的区间顶替。`GET /api/v1/analysis/runs/{runId}` 返回报告生成时的 `as_of`，解读页的规则读数与现金流对比按这个日期取数。

## 推演沙盘

全部接口需要登录，只能看到自己的记录。结果仅用于研究与教学，不会发送委托。

- `POST /api/v1/simulation/runs`：`symbol`、`as_of`、`max_debate_rounds`（1–3）、`holding_days`（5–180，自然日）、`risk_strictness`（`moderate` / `conservative`）、`use_memory`、`engine`（`rules` / `tradingagents`）。
  - `engine=rules` 是确定性规则引擎，不调用模型。
  - `engine=tradingagents` 跑已安装的 TradingAgents 原版图，没有配置 `LLM_API_KEY` / `LLM_MODEL` 时返回 409 `LLM_NOT_CONFIGURED`，不会退回 Mock 结果。
- `GET /api/v1/simulation/runs`：历史列表。`GET /api/v1/simulation/runs/{runId}`：单次结果，含分析师报告、辩论、风控修正链、决策和记忆。
- `GET /api/v1/simulation/runs/{runId}/report`：Markdown 报告。`GET /api/v1/simulation/runs/{runId}/stream`：按落库顺序重放节点（SSE）。
- `POST /api/v1/simulation/runs/{runId}/backtest`：用已入库收盘价结算，基准是沪深 300。持有期没走完时如实返回「尚未到期」。
- `POST /api/v1/simulation/experiments`：网格回测，在 `start`–`end` 内每 `step` 个交易日推演一次，比较多角色推演和单一技术面信号的命中率与多空收益差。持有期没走完或缺日线的样本放进 `skipped`，不计入统计。

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

## 行业假设

`GET /api/v1/industries` 返回目录分类。`GET /api/v1/industries/{industryId}/facts?as_of=` 只列出该行业成员在截止日之前已经入库的事实。`POST /api/v1/industries/{industryId}/theses` 保存可证伪假设，必须同时写下支持和反证条件。`POST /api/v1/theses/{thesisId}/evidence` 只能引用这些事实，引用文字由服务端按入库数字生成。同一条事实不能既当支持又当反证。`POST /api/v1/theses/{thesisId}/assessments` 在截止日同时看到支持和反证时，状态是 `contested`，说明这不是已证实的结论。别人看不到自己的假设。早于事实申报日的截止日不会把那条证据算进去。

管理员 `POST /api/v1/admin/akshare-syncs`。同一交易日的重复行只保留第一条。上游失败时 `rows_written` 为 0，已有 K 线不改。自动测试使用脚本响应，不访问外网。

## 同步任务

`POST /api/v1/admin/sync-jobs` 只负责入队。同一 `dedupe_key` 在排队或执行中时，再次提交返回原来的任务，`created` 为 false。Worker 用 `FOR UPDATE SKIP LOCKED` 每次只领一条。超时或上游失败会按次数重试；校验错误和未配置密钥不重试。失败记录保留，`POST /api/v1/admin/sync-jobs/{id}/retries` 另开一条。`GET /api/v1/admin/providers` 带上 `last_health_status`。

没有公开注册。管理员可由 `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD` 配合 `python -m app.scripts.seed` 创建；未设置这些变量时不创建账号。

## 数据就绪度与按需抓取

- `GET /api/v1/companies/{instrumentId}/data-status`（公开）：`datasets` 里是 `financials` 与 `bars` 两项，每项有 `state`（`fresh` / `warn` / `stale` / `missing` / `unavailable`）、`sync`（`idle` / `queued` / `running` / `failed`）、`latest_data_date`、`last_synced_at`、`message`、`can_request`；`syncing` 表示有任一项在排队或抓取。
- `POST /api/v1/companies/{instrumentId}/data-requests`（需登录）：缺数据或已过期时登记后台抓取并返回最新状态。队列已有同一任务时不重复登记；失败后 15 分钟内不重试，上游没有这家公司时 24 小时内标 `unavailable`；活跃任务超过 200 条返回 `QUEUE_BUSY`。
- `GET /api/v1/system/freshness`：`items` 按 `group`（`process` / `macro` / `bars` / `sync`）列出各数据源的状态，`scheduler_running` 来自调度器心跳。阈值：日线 4 天 / 10 天，财报 48 小时 / 7 天，清单 9 天 / 21 天，宏观按频率。
- A 股公司的 `/financials` 与 `/research` 数据来自东方财富 F10（`data_status=eastmoney`，概念前缀 `cn-gaap:`，累计口径）；美股来自 SEC（`edgar`）。美股日线来源 Tiingo，没配置 `TIINGO_API_KEY` 时 `bars` 为 `unavailable`。

## 账号与注册

| 方法 | 地址 | 说明 |
|---|---|---|
| GET | `/api/v1/auth/options` | 公开：`{allow_registration}`，登录页据此决定是否显示注册入口。 |
| POST | `/api/v1/auth/register` | `{username,email,password}`。永远创建 `researcher`，账号名小写化，密码至少 8 位且含字母与数字；成功后自动登录（同 login 的返回）。关闭注册时 403 `REGISTRATION_CLOSED`；同一 IP 每小时超过 `REGISTER_MAX_PER_HOUR` 次 429。 |
| POST | `/api/v1/me/password` | `{current_password,new_password}`。成功后撤销该用户所有会话再为当前设备重新签发。 |

## 密钥与数据源配置（仅管理员）

| 方法 | 地址 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/integrations` | 列出可管理项：来源（界面 / 环境变量 / 未配置）、是否已配置、掩码值（`••••a1b2`）。**永远不返回明文。** |
| PUT | `/api/v1/admin/integrations/{key}` | `{value}` 加密保存。`key` 在白名单内：`TIINGO_API_KEY`、`FRED_API_KEY`、`SEC_USER_AGENT`、`LLM_API_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`ALLOW_REGISTRATION`。 |
| DELETE | `/api/v1/admin/integrations/{key}` | 清除界面保存的值，退回环境变量。 |
| POST | `/api/v1/admin/integrations/{key}/test` | 用当前生效的值真实探测一次对应服务，只回报成功与否和原因，不回显密钥。 |

## 中国宏观与宏观状态快照

- 中国宏观六序列（CPI、PPI、PMI、M2、工业增加值、社会融资规模）由 `china_macro` 任务从东方财富数据中心与商务部公开接口抓取。接口没有发布日期，`published_at` 取保守上界（CPI/PPI 次月 15 日、PMI 次月 1 日、M2/工业增加值/社融 次月 20 日）；`GET /api/v1/macro/china/...` 的时间旅行以该日期为准。
- `GET /api/v1/macro/regime-history?limit=60`：定时任务保存的宏观状态判断快照（`as_of`、`regime_code`、增长/通胀/流动性得分、规则版本、计算时间），同一 `(as_of, model_version)` 重算只更新一行。

## 财务体检快照

`GET /api/v1/companies/{instrument_id}/metrics?as_of=YYYY-MM-DD`

返回定时任务 `financial_metrics` 写入的最新一期指标：`revenue_yoy`、`net_income_yoy`、`gross_margin`、`debt_ratio`、`current_ratio`、`cfo_to_net_income`。每项含 `value`（字符串小数，算不出为 null）、`unit`（`%` 或 `x`）、`quality_flag`（`ok` / `unavailable`）、`reason`（算不出的原因）、`published_at` 与 `inputs`（取数来源：科目、申报单号、来源链接）。只在 `published_at <= as_of` 时可见；没有真实披露的公司返回 `status=empty`，不会用样例补。
