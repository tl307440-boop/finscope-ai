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

`GET /api/v1/macro/observations?codes=UNRATE,ISM_MFG&as_of=2026-10-17` 一次取回多条序列在该观察日可见的观测，`data = { pit_date, items[] }`，`items` 顺序与请求一致，每项结构同单序列接口。`codes` 1–20 个，不认识的代码返回 422 且 `error.details.unknown_codes` 列出；limit 由频率决定，默认日度 2500、周度 600、月 / 季度 240，可用 `MACRO_BATCH_LIMIT_DAILY` / `_WEEKLY` / `_OTHER` 调整（1–20000）；读库只取该条数所需的那一段观察日，库里历史存得再长也不拖慢页面。同步的观察区间上限由 `MACRO_SYNC_MAX_WINDOW_DAYS`（默认 36500 天）控制；FRED 一次最多 2000 个版本日，同步按版本日自动拆成多次请求，值相同、首尾相接的版本合回一行，真实修订保留。一次性回补用 `python -m app.scripts.bootstrap_data --only fred --fred-years N`（默认 10 年，受区间上限约束）。

美国宏观目录共 19 条：FRED 17 条 + 东方财富数据中心 `RPT_ECONOMICVALUE_USA` 转载的 ISM 制造业（`ISM_MFG` / `EMG00002790`）与服务业（`ISM_SVC` / `EMG00002791`）PMI。目录项带 `provider`（`fred` / `eastmoney`）与 `source_key`（东方财富指标 ID）。ISM 以发布日为 `realtime_start`，与 FRED 同样次日可见；来源只给最新值，首次同步前的历史为最新修订值，之后每次同步发现变化即新增修订版本：旧值截止到抓取日（纽约日历日）当天，新值从抓取日次日起可见。东方财富返回空列表或没有一行可解析时同步报 `UPSTREAM_PAYLOAD`，已入库数据不变；同一月份多行只留发布日最晚的一行。同步仍用 `fred_series` 任务，按序列的数据源分派。

`POST /api/v1/macro/interpretation`（需登录）请求体 `{ "region": "us" | "cn", "as_of": "YYYY-MM-DD" }`，按观察日读白名单序列（美国：GDP 折年率、CPI / 核心 PCE 同比、失业率、政策利率上限、10Y、10Y−2Y；中国：GDP、工业增加值、社零、CPI、核心 CPI、PPI、PMI、M1、M2、出口），交给已配置的模型转述，回答里的每个数字都要出现在资料里（1–9 的个位数除外），否则丢弃。`data = { region, pit_date, status, message, text, model, facts[], notice }`，`status` 为 `ok` / `model_unavailable`（没配模型）/ `rejected`（数字核对未通过，`text` 为空）/ `error` / `no_data`；同一资料 1 小时内复用缓存；每个用户同时最多 2 个模型任务，超过返回 429。

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

`POST /api/v1/assistant/sessions` 新建会话。`POST /api/v1/assistant/sessions/{sessionId}/messages` 用 `client_message_id` 保存回答，并以 SSE 分片返回。未配置模型时回答固定说明不会编造财务数字。已经完成的回答在取消后仍然保留。其他用户不能读取会话。

一轮问答分两步落库：先在一个很短的事务里写入用户消息和一条 `completion_status=streaming` 的空回答并提交，再在后台线程调用模型，结束后把这条回答改成 `completed` 或 `failed`。模型调用期间不占数据库事务。客户端断开不会取消这一轮，回答照常写完，之后用同一个 `client_message_id` 重发即可拿到结果。

SSE 响应头带 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no`。每行 `data:` 是一个 JSON，`type` 为 `delta`（`delta` 字段是文本片段）、`done`（`status` 为最终状态，同时保留 `done: true`）或 `error`（`code`、`message`）。模型迟迟不返回时，服务端每 `ASSISTANT_HEARTBEAT_SECONDS` 秒发一行 `: ping` 注释，客户端应忽略。

- 同一个 `client_message_id` 再次发送：已完成的回答原样重放，不再调用模型；失败的回答先重放一个 `error` 事件再给 `done(status=failed)`；仍在生成时返回 409 `TURN_IN_PROGRESS`。
- 同一用户同一时刻只允许一轮在生成，再发新问题返回 429 `RATE_LIMITED`（`message` 提示等上一条回答完成）。409 `TURN_IN_PROGRESS` 只用于重发同一个 `client_message_id` 而它还在生成。
- 限流返回 429 `RATE_LIMITED`，`message` 为可直接展示的中文说明。三种情况：每分钟次数超过 `ASSISTANT_PER_MINUTE`（进程内滑动窗口）、当日（按上海日历日）回答数超过 `ASSISTANT_DAILY_LIMIT`（按数据库计数）、后台并发已满 `ASSISTANT_WORKERS`（直接拒绝，不排队）。被限流的请求不写任何消息。
- 处于 `streaming` 超过 `ASSISTANT_STALE_SECONDS` 的回答（例如进程重启留下的）在读取时按 `failed` 返回，下次写入时落成 `failed`。
- 取消 `streaming` 中的回答会把它标为 `cancelled`；`completed`、`cancelled`、`failed` 视为终态，取消不会改写。

### 数据工具（`ASSISTANT_ENABLED`，默认关）

总开关在环境变量 `ASSISTANT_ENABLED`，管理员也可以在「集成设置」里改（5 秒内生效，不用重启）。**关闭、或没配模型时，行为与上面的 1a 完全相同**：只有 `delta` / `done` 事件，回答是白名单宏观转述或固定说明。

打开并配了 `LLM_*` 后，模型按需调用数据工具（宏观序列、证券搜索与日线、财务指标、行业强弱、自定义指标 PCR / 估值、我的自选、`calc`），回答只能依据查到的资料。SSE 在上面三种事件之外多出：

- `step`：`id`（`s1`…）、`label`、`status`（`running` / `ok` / `stale` / `unavailable`）。同一个 `id` 先 `running` 后变终态。
- `block`：`block` 是服务端生成的渲染块，`type` 为 `line_chart`、`metric_cards`、`table` 或 `notice`。数据块在模型写叙述之前就推出，块里的数字来自数据库，不经过模型。
- `delta`：**一句话通过核对后才推这一句**。每句里的数字、日期必须能在已查到的资料里找到，对不上的句子改写一次，仍不通过就隐藏后文，并给一个 `notice` 块（`code=NOT_GROUNDED`）。没有通过核对的文字不会出现在浏览器里。
- `done`：多出 `as_of`（资料截止日）、`model`、`grounding`（`full` 全部通过 / `partial` 有句子被拦、后文已隐藏 / `none` 一句文字都没推，只给数据块）。`failed` 的收尾仍是 `error` 加 `done`，不带这些字段。

旧前端只读 `delta` 与 `done`，忽略 `step` / `block`，照样能拿到正文和结束信号。

回合结束后这些内容与 `content` 一起存进回答行。`GET /api/v1/assistant/sessions/{sessionId}/messages` 的每条消息多出可选字段 `blocks`、`steps`、`as_of`、`grounding`、`sources`（目前恒为空，来源标注在后续期次）。没有这些内容的旧消息和用户消息**字段缺省**（接口统一不输出 `null`），前端按纯文本显示。同一个 `client_message_id` 重发时，已存的步骤、块、正文与结束信号按事件原样重放，不再调用模型。

数据块在推送之前先追加写入回答行，所以回合中途断开或被取消，已推出的块也能从库里还原；取消后不再写叙述。回合总时长超过 `ASSISTANT_TURN_SECONDS`（默认 150 秒）时不再写叙述，只给已查到的数据并带 `NOT_GROUNDED` 提示；一条可用的数据都没查到就按 `failed` 处理。模型错误、超时、异常文本都不进响应和日志（日志只记异常类型）。

时点：美国序列和美股按纽约日历日，A 股与中国宏观按上海日历日；发布当天的数据在当天不可见。自定义指标（PCR、估值分位）只回答当前，要求历史时点会得到 `unavailable` 并说明原因。

## 资料库

`GET /api/v1/analysis/reports?q=` 按标题筛选当前用户的研究记录，按创建时间从新到旧排列。`DELETE /api/v1/analysis/reports/{reportId}` 只隐藏自己的记录。`GET/POST /api/v1/watchlists` 管理自选分组，加入和移出证券也只作用于自己的分组。其他用户看到的列表是空的，删除别人的记录或自选会得到 404。自选里除沪深京个股，还能存东财的板块、指数、美股、ETF、国债收益率等（按东财 `市场.代码` 保存，列表条目带 `item_id / secid / kind / kind_label`，这类条目没有 `instrument_id`）：`POST /api/v1/watchlists/{id}/items/batch` 先匹配本系统证券、匹配不上就按东财代码保存，返回 `added / existing / external / unnamed / invalid`；`DELETE /api/v1/watchlists/{id}/items/{item_ref}` 的 `item_ref` 为条目 id（也兼容证券 id）；`GET /api/v1/watchlists/{id}/insights` 的 `others / others_section` 单列这类标的，不进入 A 股汇总。

## 东方财富绑定

扫码绑定东方财富账号后，把东财自选（全部分组）单向同步成本系统的自选分组。全部接口需要登录，只作用于当前用户。登录凭证只加密保存在服务端，任何接口都不返回 cookie 或令牌。

- `GET /api/v1/eastmoney`：绑定状态。`bound=false` 时只有该字段；已绑定时返回 `status`（`active` / `expired`，`expired` 表示东财登录已失效，需要重新扫码）、`alias`、`bound_at`、`last_sync_at`、`message`、`groups`、`items`。
- `POST /api/v1/eastmoney/qr`：生成扫码二维码，返回 `login_id`、`qr_png_base64`、`expires_in`（秒）。同一用户 3 秒内重复请求返回 429。
- `GET /api/v1/eastmoney/qr/{login_id}`：查询扫码状态，`state` 为 `waiting` / `expired` / `success`。成功时后端已保存登录态并立即同步一次，返回 `alias` 与 `sync`；首次同步失败时 `sync_error` 给出原因，绑定仍然成立。
- `POST /api/v1/eastmoney/sync`：立即同步，返回 `groups / items / added / removed / message / synced_at / skipped_groups / cached`。同一用户 30 秒内重复调用直接返回上次结果（`cached=true`）。未绑定返回 404，登录已失效返回 409，东财上游出错返回 502（绑定状态里记录原因，不删任何数据）。
- `DELETE /api/v1/eastmoney`：解除绑定并立即删除登录凭证，已同步的自选保留；未绑定返回 404。

同步规则：分组按东财分组 id 匹配（同名且未绑定的分组会被认领）；条目带 `source`，同步只移除 `eastmoney` 来源且东财里已删掉的条目，手动添加的永不被同步删除；任何失败或东财返回空列表都不会删除数据。配置项 `EASTMONEY_SYNC_ENABLED`（默认开）与 `EASTMONEY_SYNC_INTERVAL_SECONDS`（默认 300）控制后台定时同步。

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

## 总览驾驶舱

每个接口各管一个模块，互不阻塞；模块内每个上游各带一个 `Section`（`status` ok / stale / unavailable、`reason`、`fetched_at`、`origin`），某个上游失败只让对应字段为空并写明原因。百分数单位 %，金额单位元（外盘为各自报价单位），利率变化单位 bp。规则分统一为 `ScoreBlock`：`method`、`score`（可用权重 < 50% 时为 null）、`label`、`coverage`、`factors[]`（`weight`、`score`、`raw` 原始数据、`formula` 换算、`contribution` 贡献）、`missing[]`，以及读自 `dashboard_daily_metrics` 的 `previous` / `previous_date` / `change`（无入库记录时为 null）。

| 方法 | 地址 | 说明 |
|---|---|---|
| GET | `/api/v1/dashboard/market` | 公开。`temperature`（market-temperature-v2：趋势 30% 中证全指均线与 20 日涨跌、广度 35% 东财涨跌分布、活跃度 20% 两市成交额 / 前 20 日均额与封板率、波动 15% 20 日波动分位）；7 个主要指数快照；`breadth`（23 档涨跌分布）；`limits`（涨停 / 炸板 / 跌停、封板率、最高连板）；两市成交额近 60 日与 MA20。盘中量比按已交易分钟折算。 |
| GET | `/api/v1/dashboard/index-bars?symbol=sh000001&period=min\|day\|week\|month` | 公开。腾讯分时（每分钟成交额）或原生日 / 周 / 月 K（含 MA5/10/20）；日 K 带 `amount_vs_5d_pct`。只接受主要指数与中证全指，其余 422。 |
| GET | `/api/v1/dashboard/macro` | 公开。`liquidity`（global-liquidity-v1，越高越松：美联储净流动性、美元指数、10Y 实际利率、高收益债利差、NFCI、SHIBOR 隔夜、中美 10Y 利差）、`growth`（global-growth-v1：失业率变化、非农 3 月均值、中国 PMI）、`regime`（经济热度 × CPI 同比动能）、4 个指标卡、9 个外盘资产及弹窗用序列。FRED 公开 CSV 首次约 15 秒，之后走缓存。 |
| GET | `/api/v1/dashboard/boards` | 公开。`boards`（board-heat-v1：0.4×同花顺热度分 + 0.3×涨幅分位 + 0.3×涨停家数分；热度 ≥ 60 且涨幅 ≥ 1% 计为热点）、`hot_count` / `hot_count_change`、`gainers`（新浪行业与概念板块涨幅榜，成分 ≥ 5）。 |
| GET | `/api/v1/dashboard/stocks` | 公开。`hot`（同花顺 1 小时热榜前 10 + 腾讯行情）；`gainers`（新浪涨幅榜，剔除 N / C 新股与成交额 < 3 亿）。 |
| GET | `/api/v1/dashboard/announcements` | 公开。巨潮资讯全市场检索（上一工作日至今，9 组分页 / 类别 / 关键词查询）；标题规则分 1–3 级，同股同类只留标题最短一条；`hot` 按热榜 / 涨跌停 / 涨幅榜给热度。部分查询失败时 `section` 为 stale。 |
| GET | `/api/v1/dashboard/watch?group_id=` | 需登录。缺省取第一个有股票的自选分组；`strong` / `neutral` / `weak` / `unknown`（近 5 日 ≥ +3% / 其余 / ≤ −3% / 无数据），前 12 只带近 10 日收盘与涨停、炸板、跌停、热榜、±5% 异动信号。他人分组 404。 |

## 财务体检快照

`GET /api/v1/companies/{instrument_id}/metrics?as_of=YYYY-MM-DD`

返回定时任务 `financial_metrics` 写入的最新一期指标：`revenue_yoy`、`net_income_yoy`、`gross_margin`、`debt_ratio`、`current_ratio`、`cfo_to_net_income`。每项含 `value`（字符串小数，算不出为 null）、`unit`（`%` 或 `x`）、`quality_flag`（`ok` / `unavailable`）、`reason`（算不出的原因）、`published_at` 与 `inputs`（取数来源：科目、申报单号、来源链接）。只在 `published_at <= as_of` 时可见；没有真实披露的公司返回 `status=empty`，不会用样例补。

## 自定义数据分析

全部公开接口，前缀 `/api/v1/custom-analysis`。数值（PCR、均线、分位、ERP、巴菲特指标、中位数 PB、区间判断）都由后端算好，前端只排版。响应里为空的字段不会出现，缺数据时 `status` 是 `unavailable` 并给 `reason`，不会用估算值补。完整口径见 `docs/superpowers/specs/2026-10-18-custom-analysis-design.md`。

- `GET /pcr/{group}?days=750`：`group` 是 `sse50` / `csi300` / `csi500` / `csi1000` / `star50`，未知分组返回 404，`days` 取 20–2500。
  - `baskets` 里 ETF 期权（`kind=etf`）与中金所股指期权（`kind=index`）分开给，不混算；中证1000 只有股指期权（MO），科创50 只有 ETF 期权（上交所 588000 + 588080 加总）。
  - 每个篮子带 `status`（`ok` / `stale` / `unavailable`）、`reason`、`first_date`、`last_date`、`history_years`、`incomplete_days`（因成员缺数据未计入的天数）、`latest` 与 `points`。
  - 点上有成交量 / 持仓量 PCR、`volume_pcr_ma10`、`volume_pcr_percentile_5y`、`sentiment_signal`（固定阈值 1.2 / 0.6）、`relative_signal`（5 年分位 ≥ 90 / ≤ 10）、`quality_flag`。窗口内不足 750 个交易日时不给分位与相对信号。
- `GET /valuation?days=1300`：`indicators` 依次是 `erp`、`buffett`、`median_pb`。
  - 每项带 `definition`、`zone`、`percentile_note`、`inputs`（每个输入的来源、最新日期、行数）和 `notes`。
  - 输入缺失时该指标 `status=unavailable`，不返回数字。最新点过期（距今超过 6 天）时 `status=stale` 并写明最近日期。
  - `median_pb` 的点带 `median_without_st_filter`（不剔除 ST 的对照值）和 `st_basis`（`snapshot_name` 或 `current_name`，后者表示 ST 剔除按当前简称近似）。
- `GET /sources`：数据源清单（每个来源的状态、区间、行数、`review_rows`、抓取时间；季频的 GDP 超过 200 天才算过期）和最近 20 条被质量闸门标记的问题。
- `GET /export/pcr/{group}.csv?kind=etf|index&days=2500`：PRD 的 `pcr_monitor` 列。没有数据时只有表头；该宽基没有这一类期权（如中证1000 的 ETF 期权、科创50 的股指期权）返回 404。
- `GET /export/valuation.csv?days=2500`：PRD 的 `macro_valuation_summary` 列。