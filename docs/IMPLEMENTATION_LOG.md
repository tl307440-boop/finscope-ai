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

## 2026-10-15 T05 FRED 宏观序列

### 本轮范围

接入 FRED 观测和修订窗口，提供宏观序列目录和按历史日期查询的接口。没有密钥时不同步，也不写入演示观测值。

### 变更

- 目录包含 GDP、UNRATE、PAYEMS、CPIAUCSL、PCEPILFE、FEDFUNDS、DGS2、DGS10、T10Y2Y。频率、单位和季调方式按 FRED 系列页核对，只保存定义。
- 未配置 `FRED_API_KEY` 时，目录和数据源状态都是不可用。同步接口返回 `DATA_UNAVAILABLE`，不会请求上游。
- 有密钥时，管理员可以按观察日区间同步。测试用脚本传输代替外网。
- vintage 只精确到日期。发布日当天还看不到该次修订，要到纽约日历的次日。后一次修订不会出现在更早的 `as_of`。

### 验证

- Ruff、Mypy 通过。
- `uv run pytest` 94 项通过，包含无密钥不写库，以及 1.25 在修订发布次日才被 9.75 替换。这两个数是合成修订样例。
- 前端测试 31 项通过，类型检查通过。

### 已知局限

- 发布日当天整日都视为尚未可见，因为 FRED 只给日期、不给时分。
- 单次同步最长 10 年观察日，避免一次拉完全部历史。
- 本机没有 FRED 密钥，运行中的页面不会出现真实观测值。

### 下一项

T06：接入 AKShare 日线。可以查询指定 A 股历史 K 线；没有外网时自动测试仍然通过。

## 2026-10-15 T06 A 股日线

### 本轮范围

接入 AKShare 使用的东方财富日线，标准化后幂等写入，并提供三只指定 A 股的查询接口。自动测试不访问外网。

### 变更

- 指定证券是 `600519.SH` 贵州茅台、`601318.SH` 中国平安、`000858.SZ` 五粮液。交易所来自证券表，不用代码首位猜测。
- K 线字段顺序按上游日线：日期、开盘、收盘、最高、最低、成交量、成交额。成交量单位是手。
- 同一交易日的重复行只保留第一条。OHLC 不合法的行不入库。再次同步同一根 K 线不会多出一行。
- `as_of` 使用上海日历日。这一天之后的 K 线不返回。
- 日线页和数据源页可以查看、同步。没同步时页面说明不会用演示价格填补。

### 验证

- Ruff、Mypy 通过。
- 日线单测和集成测试通过。集成测试用脚本 K 线，不访问外网；失败同步不改已有收盘价。
- 前端测试与类型检查通过。

### 已知局限

- 本轮只开放这三只股票的同步，其他代码会拒绝。
- 不复权是默认口径。前复权和后复权可以在同步参数里指定，页面默认查不复权。
- 交易日连续性留到 K 线页面任务。

### 下一项

T07：后台同步任务。单 Worker 队列、状态进度、失败重试和数据源健康。同一逻辑任务不能并发写重。

## 2026-10-15 T07 同步任务队列

### 本轮范围

把日线同步放进单 Worker 队列。同一逻辑任务在排队或执行时不能再开一条去写。失败可以重试，历史失败记录留下。

### 变更

- 活跃任务的幂等键使用部分唯一索引。完成后可以再次提交同一窗口。
- Worker 用行锁跳过已被领走的任务，并给执行中的任务一个租约。
- 超时按次数重试。未配置密钥、参数错误不再重试。意外异常只记“任务执行失败”，不保存连接串。
- 失败任务的重试是新的一条，原来的失败状态还在。
- 数据源页显示最近检查结果，日线同步改为加入队列。

### 验证

- Ruff、Mypy 通过。
- 后端测试通过，包含重复入队、第二个人领不到锁、重试次数和失败记录保留。
- 前端测试与类型检查通过。

### 已知局限

- 任务执行期间心跳会等这一轮结束。单日同步很短；一次拉很多年可能让就绪检查暂时变红。
- 租约 300 秒，更长的任务有被另一个 Worker 再次领取的可能。当前部署只有一个 Worker。

### 下一项

T08：Vue 主布局、主题、登录和首页状态卡。1440 和 390 宽度都要能看，没有数据时提示要明确。

## 2026-10-15 T08 主布局

### 本轮范围

补上全站侧栏、顶栏、深浅色主题和涨跌图例。首页状态卡保留加载、失败和空数据提示。

### 变更

- 宽屏侧栏 232px，可以折到 72px。宽度低于 768，包括 390，导航改成顶部横滑，内容单列。
- 默认深色。浅色写在本地，刷新后还在。
- 顶栏写明数据时间不是行情实时承诺。涨用红色加号，跌用绿色减号。
- 业务数据卡片继续说明日线和宏观没有同步时不展示数字。

### 验证

- 前端测试与类型检查通过，包含 1440 侧栏和 390 单列的宽度判断。
- 首页原有状态卡测试仍然通过。

### 已知局限

- 主题只存在这台浏览器，没有登录时不会写回账号偏好。
- 登录页也在同一套壳层里。

### 下一项

T09：美国宏观页要能看历史图、公布时间和规则分数。历史 as_of 不能看到当时尚未公布的数据。

## 2026-10-15 T09 宏观图和规则分

### 本轮范围

美国宏观页在已有观测上画折线，并给出增长、通胀、流动性的规则分和证据。历史日期只使用当时已经公布的修订。

### 变更

- 至少两个已公布的点才画线。没有点时沿用“该日期还看不到已公布的数值”。
- 增长分只比较 GDP 相邻两期，不把非农就业的水平值加进去。通胀看 CPI，流动性看联邦基金利率升降。
- 证据写出观测日、数值和公布日。分数缺失的原因是 `insufficient_data`。
- 页面写明这是规则计算，不是经济定论。

### 验证

- 规则单测通过：GDP 上升得 70，就业下降不进入增长证据；只有一个点时不给分。
- 1776-07-04 的评估接口返回 `insufficient_data`，响应里没有编造的分数。
- 前端折线和分数字符串测试通过，类型检查通过。

### 已知局限

- 没有 FRED 密钥时图和分数都是空的，这是预期状态。
- 分数只有 30、50、70 三档，不是连续模型。

### 下一项

T10：个股和指数 K 线，图例、复权、均线、收益率和成交量。数据从接口读取，不显示未来 K 线。

## 2026-10-15 T10 K 线

### 本轮范围

日线页画出 K 线和成交量，并显示 MA5/10/20/60、简单收益率和复权口径。价格来自已经同步的接口数据。

### 变更

- 均线只对已返回的收盘价做算术平均。不足 20 根时 MA20 不出现。
- 第一根没有前收，收益率留空。之后用相邻两根已有 K 线，不把中间缺失日补成 0。
- 图例标明不复权、前复权或后复权，以及涨红加号、跌绿减号。
- 查询范围加上上证指数、深证成指、创业板指和沪深300。没有同步过的代码仍然是空状态。

### 验证

- 均线和收益率单测通过。
- 前端确认两根不连续日期只生成两根 K 线。
- 类型检查通过。

### 已知局限

- 页面默认只有不复权数据。切到前复权或后复权时，如果没同步过该口径，就是空列表。
- 指数需要单独加入同步队列后才有 K 线。

### 下一项

T11：SEC EDGAR。解析 CIK、filings 和 companyfacts，并保留来源。没有外网时用一份 fixture 测通。

## 2026-10-15 T11 SEC 样例

### 本轮范围

解析一份本地 SEC companyfacts 样例，按单位、期间和申报版本选择事实，并在页面上查看来源。测试不访问 SEC。

### 变更

- CIK 补成 10 位。样例挂在 `0000320193`。
- 同一概念和期间只保留观察日之前最近一次申报，不按文件里的先后取值。
- 美元和股份分开。修订在申报日之前不可见。
- 相同内容再次入库不会重复写事实。
- 真要请求 SEC 时必须带 User-Agent，并且两次请求之间至少间隔 0.11 秒。没有 User-Agent 不会发出请求。
- 页面写明这是本地样例。

### 验证

- 解析单测通过：2024-03-01 看到 100，2024-06-01 看到修订后的 120。
- 入库集成测试通过，第二次写入 0 行。
- 前端来源字符串测试和类型检查通过。

### 已知局限

- 样例里的 100 和 120 不是苹果公司的真实收入。
- 页面目前只打开这一份样例，还不会按公司搜索全部 SEC 申报。

### 下一项

T12：财务事实和规范指标。累计转单季、TTM、估值和异常规则，边界测试要通过。

## 2026-10-15 T12 财务指标

### 本轮范围

实现累计转单季、TTM、估值和六条异常规则，并记下可复算的指标行。缺数据返回原因，不用 0 代替。

### 变更

- Q1 直接取累计。Q2、Q3、Q4 用后一个累计减去前一个累计。缺前期或版本不一致时不算。
- 资产负债表保持时点值，不做季度相减。
- TTM 只接受四个单季。同比在前期小于等于 0 时不给百分比，改写“由负转正”或“亏损扩大”。
- 收入或资产不是正数时，毛利率和资产负债率不算。亏损的市盈率显示 N/A。
- R001 到 R006 在数据不足时是 not_evaluated，不会当成没有风险。积极信号写明不是买入建议。

### 验证

- 边界单测通过，覆盖四个季度、缺 Q1、修订不一致、资产负债表、TTM、同比、估值和规则。
- 试算接口返回 Q2 单季 120，利润为负时市盈率是 N/A。
- 指标表可以写入并读回一条样例，测试结束后删除。

### 已知局限

- 页面上的 80 和 200 是计算样例，不是某家公司的报表。
- 还没有把 SEC 事实自动映射成这些规范指标。

### 下一项

T13：公司研究页。可以选两个报告期，看到每个指标的来源。不用 AI 也能看亮点和风险。

## 2026-10-15 T13 公司研究

### 本轮范围

公司研究页可以选择两个报告期，对比样例收入，并列出每条数字的申报来源。亮点和风险不调用 AI。

### 变更

- 样例增加 2022 全年收入 80，用来和 2023 年的修订值 120 对比。
- 对比按时间先后排列。每个值保留 accession、申报日和来源地址。
- 收入同比可以算出来，但缺少现金流和毛利率，所以 R006 仍是未评估，不会显示成买入理由。
- 没有可比数据时，页面写明这不表示没有风险。

### 验证

- 对比单测通过。
- 公司研究接口返回早期 80、后期 120 和各自申报号；相同报告期返回 422。
- 原有 SEC 样例测试改为按 2023 报告期核对修订，仍然通过。
- 前端来源文案测试和类型检查通过。

### 已知局限

- 只有这一份本地样例，不是苹果公司的真实收入。
- 没有应收、存货、现金流和负债，所以风险规则都还不能触发。

### 下一项

T14：PDF 上传、安全校验、去重和任务状态。大文件、非法格式、重复文件和扫描件要有明确状态。

## 2026-10-15 T14 PDF 上传

### 本轮范围

登录用户可以上传 PDF。校验魔数、大小、页数和是否为扫描件，并按内容摘要去重。

### 变更

- 文件头不是 PDF 时拒绝，即使扩展名是 `.pdf`。
- 超过大小或页数限制时不保存。
- 同一用户再次上传相同内容，返回已有记录，磁盘上仍是一份。
- 没有文字流的 PDF 标成待识别，不假装已经抽出正文。
- 损坏文件记下失败状态，不写入存储路径，也不会进入后续解析。
- 保存名是摘要，不用用户给的文件名，避免路径穿越。

### 验证

- 校验单测覆盖格式、大小、页数、扫描件、损坏文件和路径。
- 接口测试覆盖未登录、非法内容、重复、扫描件和损坏文件。
- 前端状态文案测试和类型检查通过。

### 已知局限

- 这一轮只登记任务，还没有抽取正文或建立向量索引。
- 页数靠 PDF 里的页面对象计数，复杂文件的页数可能和阅读器不完全一致。

### 下一项

T15：从文字版 PDF 抽正文、按页分块，并做混合检索。给定一段内容要能回到文档和页码。

## 2026-10-15 T15 PDF 分块与混合检索

### 本轮范围

文字版 PDF 按页抽出正文，切成带回页码的块，写入 pgvector，并用全文检索与向量的倒数排名融合返回文档和页码。

### 变更

- 新增 `document_chunks`。向量维度固定为 1536，模型名记为 `local-hashing-v1`。
- 扫描件和抽不出文字的文件不进索引。
- 检索先限定当前用户，并且只看 `uploaded_at <= as_of` 的文件。
- 没有共同词的向量近邻不会被当成命中。
- 上传页在接收文字版后建立索引，并可以按原句查看文件和页码。
- API 与 Worker 共用上传卷，Worker 会继续处理仍处于排队状态的文件。

### 验证

- 分块、页码、中文二字词和 RRF 单测通过。
- 接口测试用两页 PDF 检索到原句所在文档，页码区间以第 2 页结束；无关句子、过早的 as_of、其他用户和扫描件都不会返回该页。
- 中文定位句能回到同一文档的第 2 页。
- 前端页码文案测试和类型检查通过。

### 已知局限

- 本地哈希向量只保证可复现，不是外部语义模型。已填写的嵌入密钥本轮不会被调用，避免把另一种维度写进同一张表。
- 页码是块所覆盖的页区间。跨页重叠时区间会包含相邻页，不会编造不存在的页。
- 复杂字体的中文 PDF 可能抽不出文字，此时标记为待识别，不生成正文。

### 下一项

T16：AI gateway 的模型配置、限额、超时、类型校验和引用校验。Mock 模型给出的有效和无效 JSON 都要有明确结果。

## 2026-10-15 T16 模型网关

### 本轮范围

模型输出先过结构和引用校验。没有密钥时不调用外部模型，也不把未校验内容标成已核实。

### 变更

- 提示词放在 `app/prompts/report_v1.txt`。材料中的句子只当检索内容，不当指令。
- 合法 JSON 且引用和数字都能对上材料时才接受。
- 无效 JSON、不存在的引用或不在材料里的数字会再试一次，仍然失败则是 `analysis_validation_failed`。
- 超时、额度为 0 时不编造分析。
- 公司研究页读取模型状态。当前环境未配置模型，页面会说明不会生成 AI 段落。

### 验证

- Mock 模型单测覆盖有效 JSON、一次修复成功、两次无效、错误引用、错误数字、超时和零额度。
- 前端状态文案测试和类型检查通过。

### 已知局限

- 还没有把网关接到财报分析流水线，也没有保存报告。
- 并发上限记在当前进程里，多副本部署时不会共享。
- 每日额度由调用方传入剩余量，这一轮没有单独的用量表。

### 下一项

T17：用已入库样例走完工具收集、计算、检索、总结和报告存储。

## 2026-10-15 T17 财报研究记录

### 本轮范围

用已经入库的苹果样例，从公司研究页保存一份带申报号的研究记录，并打开详情。

### 变更

- 新增 `analysis_runs` 和 `reports`。
- 记录只写入截止日可见的样例数字、申报号和用户自己的文档页码。
- 没有模型时不生成 AI 段落。模型引用校验失败时，不把模型句子写入正文。
- 其他用户不能读取这份记录。

### 验证

- 规则记录单测确认正文含 80、120 和两份申报号，不含未提供的数字。
- 模拟模型的错误数字不会进入正文；引用正确的句子可以进入。
- 接口测试从搜索苹果到保存、再读取详情，其他账号得到 404。
- 前端类型检查通过。

### 已知局限

- 样例收入仍是本地夹具，不是苹果公司的真实报表。
- 外部模型仍未接入，页面保存的是规则整理。
- 报告还不能导出 PDF，证据点击留到下一任务。

### 下一项

T18：AI 会话的流式输出、取消、断线恢复和历史问答。断线后不重复生成，已保存的结果不丢。

## 2026-10-15 T18 问答会话

### 本轮范围

登录用户可以提问。回答先保存，再按 SSE 分片返回。同一条客户端编号重放时不再生成。

### 变更

- 新增 `chat_sessions` 和 `chat_messages`。
- 未配置模型时，回答明确说明不会编造财务数字，也不复述问题里的数字。
- 已完成的回答被取消后，原文仍在。
- 问答页展示历史，刷新后仍能读到。

### 验证

- 单测确认重复问题和已取消问题都不会再次调用生成函数，分片能拼回原文。
- 接口测试对同一 `client_message_id` 发送两次，历史里只有一条助手回答；取消后原文还在；其他用户 404。
- 前端类型检查通过。

### 已知局限

- 还没有接入外部模型，回答不是对财报的临时总结。
- 取消只能保住已经完成的文本，不能中途截断一次仍在计算的外部请求。

### 下一项

T19：报告里的数字和财报引用可以点开核对，并导出 Markdown。

## 2026-10-15 T19 报告核对与导出

### 本轮范围

研究记录详情里，每个已入库数字可以点开看到申报号和来源，并下载 Markdown。

### 变更

- 从正文识别早期和后期数字、申报号、来源地址。
- 点击后在抽屉里展示这三项，不另造数字。
- 下载的文件名去掉路径字符，内容就是已保存的正文。

### 验证

- 解析单测覆盖 80、120 和空正文。
- 前端类型检查通过。
- 打开已保存的苹果样例记录，点击早期 80，抽屉显示申报号 `0000320193-23-000001` 和 SEC 来源地址。

### 已知局限

- 没有上传文档时，记录里没有可定位的 PDF 页。
- 正式 PDF 排版还没有做，这一轮导出的是 Markdown。

### 下一项

T20：自选股和报告列表，支持筛选、排序和删除，不同用户的数据互相不可见。

## 2026-10-15 T20 自选与报告列表

### 本轮范围

登录用户可以维护自选证券，并按标题查看、删除自己的研究记录。

### 变更

- 新增自选分组和成员。同名分组不能重复。
- 研究记录增加删除时间。删除后不再出现在列表和详情里。
- 资料库页可以筛选记录、加入和移出证券。

### 验证

- 接口测试确认另一个账号看不到自选和研究记录，也不能删除。
- 标题筛选能找到自己的苹果记录。
- 前端类型检查通过。

### 已知局限

- 自选只有一个默认分组“关注”，没有拖拽排序。
- 删除是隐藏，不是从数据库物理抹掉运行记录。

### 下一项

T21：行业分类、可证伪的研究假设，以及支持和反证。

## 2026-10-15 T21 行业假设

### 本轮范围

目录里有信息技术分类，并把苹果样例挂到这个分类。假设的支持和反证都必须引用已入库事实。

### 变更

- 新增行业、成员、假设、证据和状态记录。
- 引用文字按事实的数字、单位和申报号生成，不能手写一个对不上的句子。
- 截止日两边都有证据时，状态是 `contested`，并写明不是已证实的结论。
- 只有一边证据时，不把假设收成终局。

### 验证

- 状态单测覆盖双边、单边和没有证据。
- 接口测试用后期收入 120 做支持、股数 50 做反证，更新后为 contested；2023-01-31 看不到这些申报，状态回到 open；其他用户列表为空。
- 前端类型检查通过。

### 已知局限

- 行业目录目前只有信息技术，成员只有苹果样例。
- 证据来自本地 SEC 夹具，不是这次从 SEC 拉取的真实报表。

### 下一项

T22：多行业板块强弱、市场温度和可解释评分。没有足够样本时显示无法判断。

## 2026-10-15 T22–T33

### 本轮范围

补上市场温度、公告热度、中国宏观状态、站内摘要、论文材料，以及 Mock 研究推演。

### 变更

- 市场温度和板块在样本不足时返回「无法判断」。波动高位按风险计分。
- 公告只接受指定来源，按链接、标题和时间去重。没有公告时不展示情绪分数。
- 中国宏观六个序列已登记。没有带来源的 CSV 时全部不可用，社融也不填数字。
- 每日摘要只汇总已保存记录，站内显示，不外发。
- 报告页可以打印。
- 论文材料在 `docs/thesis/`。
- 推演调用已安装的 TradingAgents `1394a3f`，`TradingAgentsGraph.propagate` 跑完后写入评级。解析不到评级时是 REVIEW。茅台中报在披露日前不可见。没有委托。

### 验证

- 温度、公告、CSV、摘要、披露日和推演规则单测通过。
- 接口测试保存了一次茅台 Mock 推演，节点含 Market Analyst 和 Conservative Analyst，其他用户 404。中国宏观全部不可用。
- 前端类型检查通过。

### 已知局限

- 推演页按上游实际产出的节点画拓扑，并显示多空文本。Mock 模型不访问外部语言模型，也不读取 StockTwits 或 Reddit。
- 区间回测只用已入库收盘价。没有后续收盘价时写入「样本不足」，不填收益率。
- 实验脚本只登记规格书里的公开处分样本 002717。40 家专家标注仍缺，召回率留空，不把未标注公司写成有隐蔽风险。
- 市场温度依赖已经入库的日线。成分不够时页面就是无法判断。
- 中国宏观和社融没有导入正式文件，所以保持不可用。

## 2026-10-15 T34–T35

### 本轮范围

落地规格书 §11「任务调度设计」与 §12.1 的上传安全检查，并按技术选型表把文档解析换成 PyMuPDF + pdfplumber。只做这两处，不动研究看板、宏观接口和中国宏观仓库。

### 变更

- 新增 `backend/app/services/scheduler.py`：APScheduler 把 §11 表格里的 7 个任务登记成 job（`sync_fred_series` / `sync_a_stock_bars` / `sync_us_sec_filings` / `parse_document` / `compute_financial_metrics` / `compute_macro_regime` / `refresh_hotspot_events`）。每个 job 只做一件事：按该行的幂等标识往 `sync_jobs` 写一条 `queued` 记录，自己不抓数据、不解析文件。
- 幂等标识逐行对上表格：`provider+series+date`、`provider+instrument+trade_date+adjustment`、`company+filing accession`、`sha256+parser_version`、`instrument+period+metric_version`、`as_of+model_version`、`date+source`。
- 幂等由数据库唯一约束兜底：沿用 `20261015_0005_sync_job_lease.py` 建的部分唯一索引 `uq_sync_jobs_active_dedupe`（`ON sync_jobs(dedupe_key) WHERE status IN ('queued','running')`），配 `SyncJobRepository.enqueue` 的先查后插，`IntegrityError` 再查一次。同一幂等键重复触发只有一条 queued 记录；失败或完成后同一键可以重新登记，历史行保留。
- 交易日判断复用已有 `trading_calendars`（`InstrumentRepository.calendar_open`）。A 股按「上一交易日收盘后延迟 `SCHEDULER_A_STOCK_CLOSE_DELAY_MINUTES`」登记；日历没有记录时打印 `unknown` 并跳过，不用星期几顶替交易所日历。
- 新增进程入口 `backend/app/workers/scheduler.py`（`python -m app.workers.scheduler`）：SIGINT/SIGTERM 走 `scheduler.shutdown(wait=True)`，按 `stopped` 写心跳后返回 0。它和 worker 分开跑，谁重启都不影响另一个。
- FRED、SEC、计算类任务未接执行器时只告警不建任务（`scheduler task unavailable`），不写假成功；`SCHEDULER_ENABLE_*` 默认只开已接好的 fred / a 股 / 解析三类。
- `backend/app/services/pdf_text.py` 重写：主提取器 pdfplumber（正文 + 表格，表格转成可检索文本），PyMuPDF 作第二提取器并做页数快速校验，pypdf 降为最后回退；返回值带 `parser_name` / `parser_version`。历史接口 `PageText` / `extract_pages` / `has_extractable_text` / `PdfTextError` 签名不变，`pdf_chunks`、`document_index` 不用改调用方式。
- `backend/app/services/pdf_upload.py` 补 §12.1 校验：魔数、mime、大小上限、最大页数、解析时间预算，外加加密与损坏各自的状态码（`REJECTED_MIME`、`ENCRYPTED`、`DAMAGED`、`REJECTED_BUDGET`）。超限是结构化 `AppError`，不是 500。
- 新增配置项 12 个（`UPLOAD_PARSE_TIME_BUDGET_SECONDS`、`SCHEDULER_ENABLED`、`SCHEDULER_TIMEZONE`、`SCHEDULER_RUN_ON_STARTUP`、各任务开关与触发时刻等），`backend/.env` 与 `.env.example` 都带中文注释，`Settings` 里加了时区名与时间预算的校验。
- 文档、启动方式和 compose 说明同步：`README.md` 补调度器启动语句，`docker-compose.yml` 加一段注释掉的可选 `scheduler` 服务（不改现有 worker 语义）。

### 验证

- `alembic upgrade head` 无待执行迁移（head 是同事的 `20261015_0017`）；本任务不需要新迁移——`lease_expires_at` 与幂等唯一索引在 `0005` 就已经存在。
- `ruff check .` / `ruff format --check .` / `mypy app` 全绿（mypy strict，141 个文件零错误）。
- `pytest` 210 例通过（新增调度器与 PDF 解析用例）。
- 真起过调度器：日志打出 `scheduler registered 3 jobs`（fred / a 股 / 解析）以及 `scheduler tick done task=sync_a_stock_bars created=7`，数据库里确实出现 7 条 `queued`，幂等键形如 `akshare:600519.SH:none:2026-10-14:2026-10-14`；同一键再触发一次是 `created=0`。SIGINT 后日志是 `scheduler stopped jobs=3` + `Scheduler has been shut down`，退出码 0。
- 自检写入的任务行与占位数据源已清理，`trading_calendars` 里为自检补的「2026-10-14 开市」记录保留（交易日历本来就是这么入库的）。

### 已知局限

- `compute_financial_metrics`、`compute_macro_regime`、`refresh_hotspot_events` 只有登记与幂等键，执行器还没接，默认 `SCHEDULER_ENABLE_*=false`，触发时只告警。`refresh_hotspot_events` 还要先有合法来源（`SCHEDULER_HOTSPOT_SOURCE`）。
- `sync_us_sec_filings` 的执行器依赖 SEC 公平访问策略下的限速，当前默认关闭。
- pdfplumber 的表抽取按行拼成文本入库，不保留单元格坐标；扫描件仍走 `OCR_REQUIRED`，没有引入 OCR。
- 本轮未跑前端构建，前端未改动。

## 2026-10-15 T23/T24 热点事件与中国宏观数据通路

### 本轮范围

把规格书里写了、服务层也写了、但没有数据通路的两条链路接上：中国宏观的正式 CSV 导入，以及合规公告事件的热度聚合。两条链路在库里都是零数据，本轮不填任何编造值。

### 变更

- `app/services/china_macro.py`：新增六条序列的目录 `china_series_catalog()` / `series_spec()`，`CHINA_SERIES` 改为由它派生，避免中文名和单位写两处。新增 `STALE_DAYS=60`、`is_stale()` 和 `public_status()`；`parse_macro_csv` 与 `series_status(code, stored)` 的既有语义不动。
- 新增 `app/repositories/macro_china.py`：登记 6 条中国序列到**现有的** `macro_series` / `macro_observations`（没有平行表、没有新迁移），提供 `ensure_catalog()`、`import_observations()`、`list_series_summary()`、`count_since()`。幂等靠 `(series_code, observation_date, published_at)` 三元组的存在性判断，重复行按错误明细返回。中国序列的 provider 是 `china_macro` 且 `enabled=false`，这样不会混进 `/api/v1/macro/series` 的美国目录。
- `app/api/v1/macro.py`：新增 `POST /api/v1/macro/china/observations`（`require_admin`），请求体就是 CSV 原文，返回 `accepted` / `duplicate_count` / `errors` / 每条序列导入后的状态。解析失败的行只进 `errors`，不落库。
- `app/api/v1/research_board.py`：`GET /macro/china` 改为查真实条数和最新值，按 `stored` / `unavailable` / `stale` 返回，并给出最新值、观测日、来源、公布日、已存条数。库里没有数据时六个序列如实 `unavailable`，`notice` 指向导入接口。
- 新增 `app/repositories/news_events.py`：按 `dedupe_hash` 幂等写入、按发布日期区间查询，另加 `industry_exists()` 防悬空外键。
- `app/services/disclosure_events.py`：新增来源徽标、事件类型标签、事件类型白名单、`validate_import()`、`group_by_type()`；`validate_disclosure` 仍只认 `cninfo`/`sse`/`szse`/`sec` 且必须 https。
- `app/api/v1/research_board.py`：新增 `POST /api/v1/market/hotspots/events`（`require_admin`），先过合规校验，不合规逐条报错、不落库。`GET /market/hotspots` 改为真实按日聚合，支持 `start` / `end` / `industry_id`，返回主题热度、事件时间线、来源徽标和事件明细。`enable_news=false` 时不读新闻、返回空列表并说明原因。
- 迁移 `20261015_0017`：只给 `news_events` 加 `industry_id`（可空，外键到 `industries.id`，带索引），这样热点页能按行业过滤。
- `GET /me/digest` 接上真实数据：报告标题来自 `ReportRepository.list_owned`，公告计数来自第 2 步的真实按日计数。

### 验证

在 `backend` 目录：

- `ruff check .` 全部通过；`ruff format --check .` 本文件范围内通过。
- `mypy app` 通过，141 个源文件无问题。
- `pytest -q` 212 例全通过，无失败、无跳过。
- 新增用例：`tests/unit/test_china_macro_import.py`、`tests/unit/test_disclosure_events.py`、`tests/integration/test_board_data_flow.py`（正常路径 + 缺数据/不合规路径各一）。
- 真起过一次 uvicorn。`GET /macro/china` 导入前六个序列全是 `unavailable`、`observation_count=0`；导入含一行合法 CPI 的 CSV 后 `accepted=1`、`errors` 里有“第 3 行日期或数值无法解析”“第 4 行不在已登记的中国宏观序列里”，该序列变成 `stale`（1990 年的观测，距今超过 60 天）并带 `latest_value=0.800000`；重放同一份 CSV 时 `accepted=0`、`duplicate_count=1`，说明幂等成立。匿名调用该接口返回 401。
- `POST /market/hotspots/events` 传 1 条合规 + 1 条不合规，返回 `accepted=1`、`errors=["第 2 条：这个来源不在允许的公告名单里"]`；随后 `GET /market/hotspots` 返回 `count=1` 和真实时间线。空窗口查询返回 `count=0`、空列表。`ENABLE_NEWS=false` 时返回空列表并说明数据源未开启。

### 已知局限

- 中国宏观仍然没有正式 CSV 入库，六个序列在页面上是 `unavailable`，这是真实状态，不是缺实现。
- `stale` 统一按 60 天判断，6 条序列都是月频口径，季频来源需要重新定阈值。
- 热点事件只接受 `cninfo`/`sse`/`szse`/`sec` 四个来源，其他媒体源仍然不入库。
- 本轮没有改前端，页面是否已经渲染新字段要前端同学确认。
- 集成测试之间有共享数据库状态：`sync_jobs` 里残留的 `failed` 行会让 `test_sync_jobs.py` 的领取用例失败，`test_sec_fixture.py` 清掉的申报会让后跑的 `test_industry_thesis.py` 失败。这两种失败在干净库上跑一次全量就消失，与本轮改动无关。

### 下一项

T23：把 `refresh_hotspot_events` 的执行器接上，并在拿到合规数据源许可后自动导入公告。

## 2026-10-15 T36–T38 效果图对齐、第二条行情源、显示精度

### 本轮范围

用户放进来十张效果图（`系统设计\ChatGPT 图像 2026年10月15日 21_48_*.png`），要求界面按效果图做；
同时发现行情页大面积空态，追下去是唯一的上游（东方财富 `push2his.eastmoney.com`）被限流到不可达。

### 变更

**一、把效果图固化成可执行的规范（先规范后改码）**

- 新增 `docs/UI-设计规范.md`：十张图到路由的对照表、栅格尺寸（左栏 240px / 折叠 72px、顶栏 64px、
  内容区 `max-width: 1680px` + `padding: 20px 24px`、卡片圆角 12px、间距 16px）、字号与字重、
  状态色到 token 的映射、组件清单、图表规则，以及硬约束（不编造数据、前端不做金融计算、
  空态必须写后端给的原因）。
- 新增 `frontend/src/components/ui/` 共 11 个组件：`PageHeader`、`PanelCard`、`StatCard`、
  `MetricTile`、`DeltaValue`、`StatusPill`、`SectionTabs`、`StateBlock`、`SourceFooter`、
  `MiniSpark`、`MiniRing`。迷你走势和圆环是纯 SVG —— 一页几百个实例，用 ECharts 会卡。
- `app-shell` 左栏改成与效果图一致的一行一个一级导航（当前组的子路由在下面展开），
  顶栏加全局搜索、数据时间、任务状态、通知、账号菜单，底部加数据来源条。
- 20 条路由对应的 20 个页面全部按效果图重排；没有效果图的次级页沿用同一套语言。

**二、加第二条行情源（东财不可达时的备用）**

- 新增 `app/providers/tencent_bars.py`：腾讯 `web.ifzq.gtimg.cn` 日线适配器，
  字段顺序是 日期,开,收,高,低,量（不是 OHLC），`market_code()` 按交易所给 `sh`/`sz` 前缀。
- `RawBar.amount` 与 `market_bars.amount` 改为可空：腾讯只给成交量、不给成交额。
  缺成交额时存 `NULL`，页面渲染「—」，**不用 量×价 补一个看起来合理的数字**。
- `app/services/akshare_sync.py` 的 `_SOURCES` 改成按顺序尝试的源列表，
  每个源失败都记下来，全部失败时把每个源的失败原因一起报出来（`UPSTREAM_UNAVAILABLE`）。
- 数据源行如实改名：`data_providers` 里 `code='akshare'` 那一行的 name 改成
  「A股日线（东方财富 / 腾讯行情）」，`config_public.upstreams` 列出两个上游域名。
  `code` 保持不变，因为它已经被 `market_bars.provider_id` 引用。
- 修掉一个真实缺陷：`httpx_transport` 原来只捕获 `httpx.TimeoutException`，
  上游断流（`RemoteProtocolError`）会冒泡成 500。现在转成 `ProviderDisconnected` 并可重试。
- 顺带清掉集成测试在共享库上留下的 51 行 `probe-*` 僵尸数据源。

**三、显示精度**

- `bar_indicators.moving_average` / `simple_return` 四舍五入到 4 位与 6 位：
  Decimal 除法精度是 28 位，60 日均线原来会输出 `1295.7418333333333333333`。
- `market_temperature._mean` 同样定刻度，因为 MA20/MA60 会直接写进证据串。
- `DeltaValue` 不再暴露浮点尾巴（涨跌幅固定两位），且**只有真的在表达变化时才补 `+`**：
  收盘价读 `3,707.48`，涨跌幅读 `-1.59%`。负号永远保留，色盲也能读方向。
- 后端用英文代码传单位，界面统一映射：`hand` → 手。

### 验证

在 `backend` 目录：

- `ruff check .` 全部通过；`ruff format --check .` 213 个文件已格式化。
- `mypy app` 通过，142 个源文件无问题。
- `pytest -p no:randomly` **225 例全通过**（新增 `tests/unit/test_tencent_bars.py` 13 例、
  `tests/unit/test_bar_indicators.py` 2 例）。

在 `frontend` 目录：

- `npm run typecheck` 退出码 0；`npm test` 18 个文件 48 例全通过；`npm run build` 成功。
- `npm run e2e` 15 例全通过（8 条关键流程 + 7 组截图）。

真数据验证（服务由 `windows-deploy\start.bat` 起）：

- 24 个 A 股标的全量同步成功，`market_bars` 里是真实交易日，
  例如 600519.SH 最新一根 2026-10-15 收盘 1312.97，`ma5=1355.534`、`ma60=1422.7618`、
  `simple_return=-0.002283`，没有浮点尾巴。
- 驾驶舱 A股市场温度从「—」变成 43.6/100；指数走势图上四条真实指数曲线；
  `/market/indices` 四张指数卡、K 线、均线全部有值。
- `docs/screenshots/` 重新生成 24 张，全部是真数据。

### 已知局限

- **成交额缺失**：腾讯不返回成交额，所以走备用源入库的日线 `amount` 是 `NULL`，
  页面上显示「—」。没有用 量×价 之类的办法补。
- **`source` 字段仍是 `akshare`**：`market_bars` 只存 `provider_id`，不存每根线的上游，
  所以 `GET /market/bars/{id}` 的 `source` 还是那一行的 code。要按根区分上游得加字段和迁移。
- **FRED / 中国宏观仍然是真实的空态**：`FRED_API_KEY` 与官方 CSV 都没有提供，
  六个中国宏观序列在页面上就是 `unavailable`，这是真实状态。
- **热点页是空态**：`ENABLE_NEWS=false` 且没有合规公告导入，`count=0` 会照实显示。
- **效果图里没有数据支撑的字段一律留空**：相关性、置信度、报告字数、情景概率这些
  后端没有的口径全部渲染成「未计算」/「—」，没有为了让图好看而编数。

### 下一项

拿到 `FRED_API_KEY` 后跑一次 `POST /admin/fred-syncs`，美国宏观那几张卡就会有值；
拿到官方中国宏观 CSV 后走导入接口。两者都不需要改代码。

## 2026-10-16 科技感改版、真实数据灌入、推演沙盘重做、财务计算回收到后端

### 本轮范围

用户要求：

- 按参考图 `0FA69BB5839BE75B2F355EC0EFD7FCA3.png` 把界面做成科技感风格，把后端数据补完整，并全量优化。
- 参考 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 完善多智能体推演。

参考图已经定下了风格，所以按截图还原做，不另外探索风格方向。

### 变更

**一、视觉系统（方向：夜间交易台上的精密仪器）**

- 设计变量：深海军蓝底、细网格与边缘高光、按领域着色的图标块、数字统一用等宽窄体。
- 全局 ECharts 主题 `finscope`：暗色 tooltip、统一配色与坐标轴。
- 外框：左栏选中态、顶栏搜索 / 时钟 / 数据状态，底部来源条。
- 动效只有三种，都在用户选了「减少动效」时关闭：
  - 页面内容入场时一次性轻微上浮（420ms）；
  - 加载骨架的闪烁；
  - 推演流水线运行中的扫描线，只在请求进行时出现。
- 修了两个缺陷：
  - 驾驶舱漏了 `MetricTile` 的导入，增长 / 通胀 / 流动性三格一直是空的。
  - 指数页首次渲染时崩溃，页面空白。
- 截图取证在 `docs/design-evidence/`：`pages-before/`、`round1/`–`round4-*`、`pages-after/`。

**二、真实数据（只走公开上游）**

- `app/scripts/bootstrap_data.py` 一键灌入，灌入后库里有：
  - 10,346 根 A 股日线；
  - 4,872 条 FRED 观测（没有密钥时走公开 CSV，只有最新修订值）；
  - 6,717 条 SEC 事实；
  - 505 条巨潮 / SEC 公告。
- 20 只 A 股补了申万一级行业（`sw2021-curated`）。银行、非银金融、食品饮料三个行业样本够，能算板块强弱，其余显示「样本不足」。
- 修了一个缺陷：宏观观测原来取的是最老的 N 条，页面把 2022 年的利率当成「最新」。已加回归测试。
- 修了测试安全问题：SEC 样例测试的清理语句会删掉开发库里苹果的真实 EDGAR 数据。现在只删样例行；库里有真实数据时这两例跳过并写明原因。

**三、推演沙盘（`/simulation/mvp`，后端 `app/trading_agents/`）**

- 上游图在没有模型时只会回一个 Mock 的「Hold」，不能当结论展示。所以默认引擎改成 FinScope 自己的确定性规则管线，角色顺序对齐 TradingAgents v0.6：
  - 四位分析师：基本面 / 技术面 / 宏观与公告 / 情绪。每条发现都带来源与观测日。
  - 多空按轮辩论。主导研究员核验引用，剔除没有依据的论点，并判断是否收敛。
  - 交易员提案，给出评级、配置区间、关注期和止损条件。止损条件为跌破 MA60，或回撤超过约 1.5 倍 20 日波动。
  - 风控三方评审。保守方可一票否决，最多退回 2 次，仍否决则降为防御方案。
  - 组合经理签署。
- 决策记忆：只结算持有期在新 `as_of` 之前已经走完的历史决策，超额收益相对沪深 300。
- 网格实验 `POST /simulation/experiments`：比较多角色推演和「仅技术面」单信号。
- `engine=tradingagents` 仍可选，没有 `LLM_*` 时返回 409 `LLM_NOT_CONFIGURED`，不拿 Mock 充数。
- 前端重做为：流水线拓扑、决策卡、分析师卡、辩论、风控修正链、记忆与结算、实验、历史。
  - 引用只有 https 地址才做成链接。
  - 旧记录没有分析师数据时说明原因，不渲染空壳。
- 结算按自然日计（`entry_date + holding_days`），界面写「天」，不写「交易日」。
- SPEC 新增 21.10 节，记录实现和 21.2 的差异：规则引擎保留否决，上游图不否决。

**四、财务数字一律由后端计算（撤回子代理在前端加的计算）**

改版时公司页和 AI 财报解读页的同比、毛利率、负债率和规则读数是在浏览器里算的，违反 `UI-设计规范.md`「前端不算财务数字」。子代理还在规范里自加了一条豁免。本轮全部撤回：

- 新增 `app/services/company_financials.py` 与 `GET /companies/{id}/financials`。
  - 读数卡、单季 / 全年趋势、财务表、近五年现金流与净利润、R001–R006 读数全部在后端按申报口径配对计算。
  - 基数不为正时不给百分比，与 `financial_metrics.year_over_year` 同一口径。
- `company_research.compare_periods` 给 `SourcedAmount` 补上 `period_start`，按期间长度配对（单季对单季、全年对全年）。
  - 以前只有 `*Revenues` 有变化率，现在所有同口径指标都有。
  - 口径不同时返回 `incomparable_period`。
- `GET /market/bars/{id}` 新增 `window_returns`（近 22 / 66 / 130 / 250 个交易日），公司页的区间涨跌幅改读接口。
- 前端删掉 `computeRuleInputs`、`ruleObservation`、`percentChange`、`ratioPercent`、`pickMetric`、`metricSeries`、`findYearAgo` 等计算函数。
  - 只保留口径标签（单季 / 累计）与默认报告期这类日期判断。
- `UI-设计规范.md` 第八节第 2 条恢复原文。

**五、独立复核后的修正**

只读子代理复核了上面的改动，没有发现泄漏 `as_of` 之后申报事实的问题。指出的中低级问题都已修复：

- 报告引用事实时只比较概念、单位、期末和申报号。同一份 10-Q 里单季和累计这几项相同，于是单季数值被挂到了累计事实的 id 上。现在匹配时加上期初日期。
- 净利润为负时 R002 仍给出负倍数，分母为负时毛利率之类仍给出百分比。现在都不出数，和 `financial_metrics` 的口径一致。
- R005 原来先舍入百分比、再除以 100 舍入一次，改为直接相除后四舍五入。经营现金流恰好为 0 时显示「为零」。
- 区间收益率按 N 个交易日的间隔计算，日线不够时不拿更短的区间顶替，页面也改成显示 N+1 根。
- 公司页日线请求带上观察日 `as_of`。A 股换观察日时也会重新拉日线。
- 财务总览请求加了序号，晚到的旧响应不会覆盖新数据。报告期回填不再重复触发请求。
- `/analysis/runs/{id}` 返回报告自己的 `as_of`，解读页的规则读数和现金流对比按报告当时的观察日取数，不再用今天。

### 验证

- 后端：`ruff check`、`ruff format --check`、`mypy app`（154 个源文件）全部通过。`pytest -p no:randomly` 对开发库 265 例通过，2 例按设计跳过（库里已有真实 AAPL EDGAR 数据，样例断言需要干净的 CIK）。
  - 新增单测：`test_company_financials.py`、`test_bar_window_returns.py`，`test_company_research.py` 补了口径配对用例。
  - 新增集成测试：`test_company_financials_api.py`，覆盖时间旅行、空态和 404。
- 前端：`npm run typecheck`、`npm run build` 通过；`vitest` 22 个文件 90 例通过。
  - 新增 `simulation.test.ts`、`companyFinancials.test.ts`。
- 真数据核对（AAPL，参考期末 2026-06-27）：单季营收同比 +16.36%，毛利率 50.06%（+3.57pct），资产负债率 71.95%，年度营收同比 +6.43%，R001–R006 读数齐全。
- 推演实验：宁德时代 + 贵州茅台，2025-01-02 至 2026-06-30，每 20 个交易日一次，持有 30 天，36 个样本。
  - 多角色推演：命中率 0.3704，多空收益差 −2.79%。
  - 仅技术面：命中率 0.4286，多空收益差 −2.02%。
  - 多角色版本这一轮没有跑赢对照，照实记录，没有调权重去凑结果。

### 已知局限

- **仍在前端换算的行情 / 宏观数字**：
  - 行情页与指数页的涨跌幅、均线偏离，驾驶舱指数累计涨跌，`macro.ts` 的宏观同比——这些在 HEAD 就有。
  - 宏观页「较上期」是本轮照同一做法加的。
  - 都只用接口返回的原始收盘价或观测值，找不到对比期就不出数，但与规范第八节第 2 条不一致，作为已知偏差保留。
- `engine=tradingagents` 的真实模型路径没有验证（BLOCKED：需要 `LLM_*`），只用不联网的 Mock 跑过集成测试。
- 人在回路干预、`/graph`、`/debates` 子路由未实现（NOT_IMPLEMENTED）。
- 实验只有两只股票、36 个样本，不足以下结论；深交所处分标签的实验 D 没有做。
- 旧的研究记录正文是按旧口径生成的，非营收指标的「变化」仍是「无法计算」。重新生成后才有新口径的数。

### 下一项

- 把行情涨跌幅和宏观同比移到后端，消除上面的偏差。
- 拿到模型密钥后用 `engine=tradingagents` 跑同一组网格，与规则引擎对比。

## 2026-10-16 全市场可搜索、按需抓取、A 股财报、美股日线源、调度器默认启用、新鲜度监控

### 本轮范围

用户问「搜任意公司就给研究报告」是否已实现，并要求补齐：美股用 SEC 官方 ticker 表变成全部可搜索，首次搜索在后台抓财报；A 股补财务区；补美股行情源；调度器默认启用并进 compose；监控数据新鲜度。

### 变更

**一、证券清单（`providers/universe.py`、`repositories/universe.py`）**

- 美股：SEC `company_tickers_exchange.json`，只保留 Nasdaq / NYSE，每个 CIK 一个代码（`uq_instruments_cik` 不允许重复，GOOG 与 GOOGL 只留先出现的）。
- A 股：巨潮 `szse_stock.json`，沪深主板 / 创业板 / 科创板；B 股与北交所不收。
- 只补缺，不覆盖已有行。首次灌入后美股 6,047 只、A 股 5,578 只。
- 调度器首次启动发现美股不足 1,000 只时补登记一次清单任务，不用等周日。
- 证券搜索排序：代码精确命中 → 名称前缀 → 其余。

**二、A 股财报（`providers/eastmoney_statements.py`、表 `cn_statement_facts`）**

- 东方财富 F10 `RPT_F10_FINANCE_G{INCOME,BALANCE,CASHFLOW}`（通用报表，银行 / 券商 / 保险也能取）。
- 映射成与 SEC 同形的事实（概念前缀 `cn-gaap:`）：营业收入、营业成本、毛利（`收入 − 成本`，`source_field` 写明是推导）、营业利润、归母净利润、稀释 EPS、研发费用、资产负债各项、经营现金流。
- 报表是年初累计口径：一季 / 中报 / 三季 / 年报分别是 3 / 6 / 9 / 12 个月累计，复用现有「期间长度相近才配对」的同比规则。
- 公告日用 `NOTICE_DATE`，没有公告日的行不入库，时间旅行 `filed <= as_of` 照常生效。
- `/companies/{id}/financials`、`/research`、`/analysis/filing` 统一走 `facts_for_instrument`；`data_status` 新增 `eastmoney`，页面注明来源与累计口径。
- 推演里的基本面分析师读同一批数据（桥接层只把前缀 `cn-gaap:` 改成 `us-gaap:`，数值、期间、公告日、链接不变）。

**三、美股日线（`providers/tiingo_bars.py`）**

- 没有找到不需要密钥又合规的美股日线：Stooq 现在要过 JS 校验，Yahoo 是非官方接口。选了 Tiingo 官方免费档（个人研究用，约 50 次/小时）。
- 需要 `TIINGO_API_KEY`。没配置时数据状态是 `unavailable` 并写明原因，**不造数据**。每轮最多登记 30 只，按最久未更新轮流。

**四、按需抓取与数据就绪度（`services/company_data.py`）**

- `GET /companies/{id}/data-status`（公开）：财报与日线各自的状态 `fresh / warn / stale / missing / unavailable`、是否在排队 / 抓取、最新数据日期。
- `POST /companies/{id}/data-requests`（需登录）：缺数据或已过期时登记后台抓取，优先级高于定时任务。
- 防滥用：队列按幂等键去重；失败后冷却 15 分钟；上游「没有这家公司」冷却 24 小时并标不可用；活跃任务超过 200 条返回 `QUEUE_BUSY`。
- 公司页：进入时读状态，已登录且缺数据就登记，抓取期间每 3 秒轮询，完成后自动重读；未登录只提示「登录后会自动登记抓取」。

**五、调度器与新鲜度**

- `docker-compose.yml` 新增 `scheduler` 服务，默认启用。`SCHEDULER_ENABLED=false` 或 `docker compose stop scheduler` 可关。
- 新触发器：A 股财报（每日）、美股日线（周二至周六）、证券清单（周日）；FRED 默认 9 个序列全开；SEC 与 A 股日线只刷新「被关注过」的标的。
- A 股日线不再依赖交易日历判断开市日（日历只存开市日，用完就会静默停摆），改为从最后一根日线次日补到今天，休市日上游返回空按成功收尾。
- 新鲜度（参考 dbt source freshness 的两级阈值）：日线 4 天告警 / 10 天过期，财报 48 小时 / 7 天，清单 9 / 21 天，宏观按发布频率（日 5/10、周 12/24、月 60/100、季 200/270 天）。
- `GET /system/freshness` 返回进程心跳、宏观、日线、各同步任务；数据中心页新增「数据新鲜度」面板。

**六、顺手修复**

- `rows_written` 曾出现负数（`-637`、`-7`）：psycopg 对 upsert 的 `rowcount` 返回 `-1`。仓库层改用 `RETURNING` 计数，并有集成测试覆盖。
- 财报概念补了 `RevenueFromContractWithCustomerIncludingAssessedTax`（CrowdStrike 等用这个科目），否则营收一栏为空。
- 研究页营收亮点原来只认 `*Revenues`，现在认四个营收科目名；A 股营收单位 `CNY` 也能识别。
- `bootstrap_data.py` 与调度器共用同一份 SEC 科目清单，避免两处漂移。

### 验证

- 后端：`ruff check`、`ruff format --check`、`mypy app`（165 个源文件）通过；`pytest` 对开发库全部通过（跳过 2 例，原因同上一轮）。
  - 新增 `tests/unit/test_data_readiness.py`（清单解析、东方财富报表、Tiingo、新鲜度阈值、按需抓取判定）与 `tests/integration/test_data_readiness_api.py`（清单幂等不覆盖、A 股事实时间旅行与更正、`data-status` 公开 / `data-requests` 需登录 / `freshness`）。
- 前端：`vue-tsc` 与 `vitest`（22 个文件 90 例）通过。
- 真机联调（容器重建后）：
  - 清单任务两条成功，库里 CN 5,578 / US 6,047；搜 `CRWD`、`002594`、`比亚迪` 都能命中非种子公司。
  - 登录后对 CrowdStrike 与比亚迪各发一次 `data-requests`，约 45 秒内两家财务都入库：比亚迪 2026 中报营收 3,448.15 亿元、毛利率 18.85%、资产负债率 70.96%；CrowdStrike 营收 14.7 亿美元、毛利率 74.57%。
  - 美股日线没有 `TIINGO_API_KEY`，页面与接口都显示「不可用：未配置美股行情源密钥」。
  - 未登录打开一家没抓过的美股，页面显示「尚无数据」和登录提示，没有空壳按钮。

### 已知局限

- **Tiingo 路径没有联网验证**：没有密钥，解析与错误映射由单测覆盖，真实请求未跑过（BLOCKED：需要 `TIINGO_API_KEY`）。
- A 股报表是累计口径，没有单季拆分；东方财富 F10 不是官方公开 API，字段变化时解析会报 `UPSTREAM_PAYLOAD` 而不是写错数。
- 美股只收 Nasdaq / NYSE，OTC 与 ADR 之外的外国发行人（20-F / 6-K）财报科目不全，营收等可能缺。
- 财报入库的科目清单扩充后，已入库公司要等下一次刷新（每日）才补到新科目。
- 中国宏观六个序列仍没有数据源；`financial_metrics`、`macro_regime`、`hotspot` 三类调度任务仍没有执行器。
- 公司页「立即抓取」只对已登录用户有效，匿名访问不会触发后台抓取（避免被刷）。

### 下一项

- 注册 Tiingo 免费密钥，写进 `.env` 的 `TIINGO_API_KEY` 后验证美股日线。
- 搜索框对「正在抓取」的公司给出提示，而不只在公司页提示。

## 2026-10-16 界面管理密钥、注册、中国宏观数据源、三类调度执行器、外国发行人财报

### 本轮范围

- 把密钥填写逻辑放进客户端（管理员在界面填写，加密存库）。
- 登录页补上「注册新账号」和修改密码，打通首个管理员的创建。
- 补齐中国宏观六序列的数据源。
- `financial_metrics`、`macro_regime`、`hotspot` 三类调度任务补上执行器。
- 美股外国发行人（20-F / 40-F / 6-K）的 IFRS 科目归一。
- 新增 `docs/使用说明.md`。

### 变更

**界面管理密钥**

- 新表 `app_settings`（迁移 `20261016_0021`），值用 Fernet 加密；主密钥取 `SETTINGS_ENCRYPTION_KEY`，为空用 `JWT_SECRET` 派生。
- `effective_settings(engine)` = 环境变量 `Settings` 叠加数据库里的值，数据库优先，带 5 秒缓存；api / worker / scheduler 三个进程读同一份，保存后几秒内全部生效，不需要重启。
- 管理员接口 `GET/PUT/DELETE /admin/integrations`、`POST /admin/integrations/{key}/test`；接口只返回掩码，不返回明文，不进日志；`httpx` 日志降到 WARNING（上游密钥在查询串里）。只有白名单内的键可管理。
- 前端「数据管理 → 密钥与数据源配置」面板，含测试连接、清除（退回环境变量）、开放注册开关。

**注册与账号**

- `POST /auth/register`（永远创建 `researcher`、账号小写化、密码强度校验、每 IP 每小时限额）、`GET /auth/options`、`POST /me/password`（成功后撤销所有会话再为当前设备重发）。
- API 启动时按 `ADMIN_*` 自动创建首个管理员；已存在则跳过，不覆盖密码；密码不满足策略则告警并跳过。
- 登录页新增注册入口，是否显示由 `ALLOW_REGISTRATION` / 界面开关决定。

**中国宏观六序列**

- `providers/china_macro_sources.py`：东方财富数据中心 `RPT_ECONOMY_*`（CPI、PPI、PMI、M2、工业增加值）+ 商务部 `shrzgmQuery`（社会融资规模，单位亿元）。
- 上游没有发布日期，`published_at` 取保守上界（CPI/PPI 次月 15 日、PMI 次月 1 日、M2/工业增加值/社融 次月 20 日）；按「序列 + 观察月 + 可见日」幂等写入。
- `china_macro` 调度任务（每日）；部分来源失败时保留已成功写入的，再抛 `UPSTREAM_PARTIAL`。中国序列的 `MacroSeries.enabled` 保持 False（该字段同时控制美国目录）。

**三类调度执行器**

- `financial_metrics`：对被关注的美股和 A 股逐家计算营收同比、净利润同比、毛利率、资产负债率、流动比率、经营现金流 / 净利润，写入 `financial_metrics`。只读真实披露事实（`company_facts.live_facts`），没有就写 0 行；算不出的存 NULL 并写明原因；每项带取数来源；没有自己来源的指标用整套事实最晚的披露日当可见日期，避免时间旅行时提前露面。新接口 `GET /companies/{id}/metrics`，公司页新增「体检快照」面板。
- `macro_regime`：迁移 `20261016_0022` 新表 `macro_regime_snapshots`，`(as_of, model_version)` 唯一，重算只更新该行；`GET /macro/regime-history`；宏观情景页新增「判断历史」面板。
- `hotspot_events`：只收巨潮资讯 / SEC EDGAR 官方披露（近 14 天，每个市场最多 60 家被关注公司），限速、重试、失败项如实写进任务信息；`SCHEDULER_HOTSPOT_SOURCE` 默认 `disclosures`，来源为空时如实标不可用。
- 三个开关默认开启；`system_freshness` 增加三项任务的新鲜度；任务用本地派生来源 `derived_local`。
- `facts_for_instrument`、`visible_sample_facts`、`LIVE_STATUSES` 从 `api/v1/companies.py` 移到 `services/company_facts.py`，接口、分析、定时任务共用同一口径。

**外国发行人（20-F / 40-F / 6-K）**

- `SEC_FORMS` 增加 20-F、40-F、6-K；`SEC_CONCEPTS` 之外接受已映射的 `ifrs-full` 概念。
- `services/ifrs.py`：读取时把 IFRS 概念映射成内部 us-gaap 短名（归母净利润优先于合并净利润等），并且一家公司只保留一种报告币种，不做汇率换算；库里仍保留原始概念与币种。接口、体检快照、分析师桥接都走同一归一函数。
- 修了营收概念列表里重复的 `RevenueFromContractWithCustomerIncludingAssessedTax`。
- 前端补充新台币、日元、丹麦克朗等币种名称；柱状图不再只限 USD / CNY。

**部署与文档**

- `docker-compose.yml` 透传 `ADMIN_*`、`SETTINGS_ENCRYPTION_KEY`、`ALLOW_REGISTRATION`，`JWT_SECRET`、数据库口令、`APP_ENV`、`FRONTEND_ORIGIN` 改为可由环境变量覆盖；`.env.example` 同步。
- 新增 `docs/使用说明.md`；`README.md`、`docs/API.md`、`docs/SPEC.md` 同步。

### 验证

- 后端：`ruff check`、`ruff format --check`、`mypy app` 通过；`pytest` 在开发库上全绿（仅有 2 个按设计跳过的用例，原因是缺少 AAPL 的真实 EDGAR 财报）。新增单元测试（`test_metrics_snapshot`、`test_ifrs`、`test_secret_box`、`test_account_rules`、`test_china_macro_sources`）和集成测试（`test_scheduled_executors`、`test_integrations_api`、`test_registration_api`、`test_china_macro_sync`），集成测试自己清理造的数据，并还原开发库里管理员填过的设置。
- 前端：`vue-tsc`、`vitest`（112 个）、`vite build` 通过。
- 联网实测：TSM（20-F，新台币）和 NVO（20-F，丹麦克朗）真实抓取后，体检快照能算出营收同比、毛利率等；TSM 2024 财年营收同比 33.89%、毛利率 56.12%，与公开披露一致。

### 已知局限

- 中国宏观的发布日是保守上界，不是官方真实发布日。
- 20-F / 6-K 公司只覆盖已映射的核心科目，用了自定义科目的公司对应指标会为空；不做汇率换算，界面按报告币种展示。
- 热点只统计官方披露条数；单次最多处理 60 家 A 股和 60 家美股被关注公司，其余下一轮。
- 更换 `SETTINGS_ENCRYPTION_KEY`（或未设置时更换 `JWT_SECRET`）会让库里已加密的密钥无法解密，需要管理员重新填写。
- 密钥保存、测试连接对真实第三方服务的联通性取决于使用者自己的密钥，本轮没有用真实付费密钥验证。

### 下一步

- 把宏观同比、行情涨跌幅等仍在前端换算的数字移到后端。
- 为 `ifrs` 归一补充更多科目（如利息、折旧）与 6-K 季度口径核对。

## 2026-10-16 DEPLOY 阿里云 ECS 自动部署

目标：合并到 `master` 且 `ci` 通过后，自动部署到 ECS `i-bp14vxhakpcfmn2llzyc`（121.199.8.235，杭州）。

### 变更文件

- `.github/workflows/deploy.yml`：`workflow_run`（ci 在 master 成功）+ `workflow_dispatch`；runner 构建后端/前端镜像，经 SSH `docker save | docker load` 传到服务器（服务器无需访问 Docker Hub / GitHub / PyPI），再调用 `deploy.sh`。使用 `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS` 四个仓库 Secret，主机指纹固定（`StrictHostKeyChecking yes`）。
- `deploy/docker-compose.prod.yml`：只用预构建镜像；数据库不映射端口；前端映射 80。
- `deploy/deploy.sh`：写入 `.release.env`、`up -d`、探测 `/api/v1/health/live`，失败自动回滚到上一个标签，只保留最近 3 个镜像版本。
- `deploy/env.production.example`：服务器 `.env` 模板（真实 `.env` 只放服务器，不入库）。
- `backend/app/core/config.py`、`security.py`、`api/v1/auth.py`：新增可选 `COOKIE_SECURE`。生产环境默认仍带 Secure；纯 HTTP（无域名/证书）时浏览器会丢弃 Secure 刷新 cookie，导致登录 15 分钟后掉线，因此允许显式关闭，上 HTTPS 后应移除该覆盖。
- `backend/tests/unit/test_auth_rules.py`：新增覆盖开关的单测。

### 验证

- 本机 shell 没有 uv / Python venv / Docker，**未在本机运行 pytest 与 docker compose**；单测由 CI 的 `ruff / mypy / pytest` 验证。
- 部署链路 **尚未在真实服务器上跑通**（BLOCKED：需要服务器 SSH 访问、安装 Docker、配置 Secrets、放行安全组 80 端口）。

### 已知局限

- 暂无域名与 HTTPS，使用 `COOKIE_SECURE=false` 的 HTTP 访问；上线前建议配置域名 + 证书。
- 镜像经 SSH 传输，首次约数百 MB；后续有 GHA 层缓存，但传输仍是整镜像。

### 下一步

- 在服务器上安装 Docker、创建 `deploy` 用户与 `/opt/finscope/.env`，配置 4 个 Secret，手动触发一次 `deploy` 工作流验证。

### 2026-10-16 补记：部署实测与 FRED 修复

- **部署已在真实服务器跑通**：镜像标签 `332ed9f285c6`，`api`/`db` 健康，`/api/v1/health/ready` 返回 database up、worker up，`http://121.199.8.235/` 返回 200（上文「尚未跑通 / BLOCKED」已不成立）。
- **缺陷**：配置 `FRED_API_KEY` 后，DGS10、DGS2、T10Y2Y 三条日频序列同步失败。直接请求 FRED 得到 `400 There are 5130 vintage dates in the specified real-time period: 1776-07-04 to 9999-12-31 ... exceeds the maximum ... (2000)`。
- **修复**：`providers/fred.py` 的 `realtime_start` 由 `1776-07-04` 改为 `observation_start`（观测不可能早于其观察日发布，不丢版本；6 年窗口约 1500 个版本日）。新增单测 `test_observations_url_keeps_vintage_dates_under_fred_limit`。
- **已知限制**：日频序列的观测窗口超过约 8 年仍会超出 2000 版本日上限；当前调度窗口为 6 年。
- **验证**：服务器上用同样参数补抓后，DGS10 写入 1499 条、DGS2 1497 条、T10Y2Y 1500 条；`/macro/series/*/observations` 现在无需 `as_of` 即可返回数据，且 `realtime_start` 为真实发布日（如 UNRATE 2026-10-02）。本机无 Python 环境，单测由 CI 验证。
- 新增 `docs/服务器使用与密钥配置指南.md`。

### 2026-10-16 补记：AI 助手点发送一直转圈

- **现象**：服务器上 AI 投研助手点发送后按钮一直转圈；api 与 nginx 日志里都没有对应的 POST。
- **根因**：`AssistantView.vue` 的 `ask()` 调用 `crypto.randomUUID()`，该函数只在安全上下文（HTTPS / localhost）存在；服务器当前走 `http://121.199.8.235`，它是 `undefined`，抛错发生在 `try` 之外，`sending` 已置 true 且不会复位，请求也没发出。
- **修复**：新增 `frontend/src/api/uuid.ts` 的 `newUuid()`（无 `randomUUID` 时用 `crypto.getRandomValues` 生成 v4）；`ask()` 先生成 ID 再置发送状态。新增 `tests/unit/uuid.test.ts`（3 个用例）。
- **验证**：在 `node:22-alpine` 容器里 `npm ci`、`vitest run`（28 个文件 / 115 个用例通过）、`vue-tsc --noEmit` 无错误。
- **NOT_IMPLEMENTED**：后端 `services/chat_reply.py` 的 `rules_reply` 始终返回固定句「未配置模型，不能根据这个问题编造财务数字…」，**AI 助手问答并没有接入大模型**。配置 LLM 后，只有研究报告 / 推演等走模型的路径会用到它；助手聊天要真正调用模型，需要单独实现（带白名单工具与引用校验），本轮未做。

## 2026-10-16 ASSIST 助手按已入库宏观序列调用模型

- **现象**：模型三项都已配置，`/analysis/model-status` 为 available，但助手仍回答「未配置模型」。原因是 `rules_reply` 不读配置。
- **行为**：`effective_settings` 判定模型可用后，`match_macro_codes` 只按白名单别名匹配美国宏观序列，`macro_chat_evidence` 读取当前时点可见的最近观测，再调用 OpenAI 兼容的 `/chat/completions`。模型正文里每个数字都必须出现在资料中，否则整段丢弃并改为列出观测。对不上序列、或序列尚不可见时，不调用模型。未配置模型时仍是原来的固定句。
- **变更**：`chat_reply.py`、`assistant_context.py`、`chat_model.py`、`api/v1/assistant.py`、`tests/unit/test_chat_reply.py`、使用指南。
- **验证**：`docker run` 挂载新代码到 `finscope-backend:local`，安装 pytest 后 `python -m pytest tests/unit/test_chat_reply.py -q` 为 7 passed；ruff check / format 通过；mypy 检查这 4 个文件无错误。集成测试未跑（需要 PostgreSQL）。
- **局限**：助手暂不回答公司财报类问题；不提供自由 SQL/联网工具。

## 2026-10-17 INDUSTRY 产业研究接入东财板块行情与热榜

- **现象**：`/industry` 只依赖库内少量手工维护的行业与成分，面板几乎看不出信息；不能按东财口径选行业，也没有走势、资金、估值与热度。
- **调研**：参考 akshare `stock_board_industry_*_em`（及其 2026 年东财三级行业升级 issue #7077）、板块轮动 RRG、资金流看板等开源项目，并用 REA 无头浏览器抓取东财行情中心页面实际调用的接口（`sidemenu_new.json` 行业树、`push2 clist/ulist` 行情与成分、`push2his kline`、`datacenter-web RPT_VALUEINDUSTRY_DET` 估值、`reportapi` 行业研报、`emappdata` 人气榜），以及同花顺 `dq.10jqka.com.cn` 公开热榜 JSON。
- **数据边界**：只用无需登录、无需反爬令牌的公开接口；**不接入**东财自选股（需要用户登录 cookie）、同花顺 `q.10jqka` 板块页（需要 `hexin-v` 反爬令牌，属规避限制）。不轮换 push2 编号子域名来绕频控。

### 变更文件

- 后端
  - `app/providers/eastmoney_boards.py`：东财板块树（一级 31 / 二级 128 / 三级 338 行业 + 概念）、板块行情、成分、日线、行业估值、研报、人气榜与同花顺热股/热门板块的 URL 构造和纯解析（`-`、缺字段一律为空，不补 0）。
  - `app/services/board_analytics.py`：均线、区间涨幅、相对沪深300超额、估值分位（近 5 年）、涨跌分布、按成分 `f100` 聚合子行业（市值加权涨跌）。全部 `Decimal`。
  - `app/services/market_cache.py`：进程内 TTL 缓存（同 key 并发只回源一次；有旧值时回源失败返回旧值并标 `stale`）与串行限速出口（两次请求间隔 ≥0.4s，直连不走代理，最多 2 次尝试）。
  - `app/services/industry_market.py`、`app/schemas/industry_market.py`、`app/api/v1/industry_market.py`、`app/api/v1/router.py`：`GET /api/v1/industry-market/{catalog, boards?level=1|2|3|concept, boards/{BKxxxx}, boards/{BKxxxx}/reports, hot}`。详情各区块（行情 / 走势 / 成分 / 估值）独立标 `ok|stale|unavailable` 与原因；整页核心数据完全取不到时返回 503，不返回空壳。
  - 测试：`tests/unit/test_eastmoney_boards.py`、`test_board_analytics.py`、`test_industry_market_service.py`（28 个用例，含缺字段、停牌、上游失败回退旧值、冷缓存失败报错、估值一级行业不可用等异常路径）。
- 前端
  - `src/api/industryMarket.ts`：响应收窄解析。
  - `src/views/IndustryView.vue`：层级切换（一/二/三级、概念）、涨跌热力图、动量象限（10 日 × 60 日，取东财给定区间涨幅）、主力资金排行、可排序板块表、东财人气榜 / 同花顺热股与热门板块、与库内逻辑追踪行业的跳转。
  - `src/views/IndustryBoardView.vue`（新路由 `/industry/board/:code`）：日 K + MA20/60、相对沪深300超额曲线、区间收益、成分股表（可跳公司页）、涨跌分布、子行业市值加权、PE/PB 历史与分位带、近 180 天行业研报。
  - `src/views/parts/industry/chartKit.ts`、`src/charts/index.ts`（注册 treemap）、`src/router/index.ts`、`tests/unit/industryMarket.test.ts`（7 个用例）。

### 验证

- 后端（本机 venv，未装 `tradingagents`）：`ruff check app tests` 通过；`ruff format --check` 本次新增文件已格式化；`mypy app` 仅剩 `app/trading_agents/upstream.py` 因未安装 `langchain_core` 的 4 个既有错误，本次新增文件无错误；`pytest tests/unit` 286 passed。
- 前端：`vue-tsc --noEmit` 无错误；`vitest run` 30 个文件 / 127 个用例通过。前端未配置 ESLint，未跑 lint。
- 实网冒烟（2026-10-17）：板块目录（一级 31 / 二级 128 / 三级 338 / 概念 516）、半导体 BK1036 研报 30 篇、估值（PE-TTM 94.10，近 5 年分位 49.92%，1212 个样本）、同花顺热股 100 条与热门行业/概念各 20 条均正常。**push2 / push2his（板块行情、成分、日线、人气榜股票名）对本机 IP 返回 403 / 连接中断**（调研阶段探测较多触发了频控），对应区块按设计标 `unavailable`，总览与详情页返回 503 并提示原因；未做绕过。频控解除后需再跑一次完整冒烟与页面截图。

### 已知局限

- 板块行情是实时快照，未入库，不能按历史时点回看；不满足 PIT 回测要求，只作看盘与研究线索。
- 行业估值与行业研报只覆盖东财二级行业；一级行业、三级行业与概念板块显示不可用并说明原因。
- 进程内缓存，多副本部署时各自回源；push2 频控仍可能让页面短时不可用。
- 动量象限只按东财给定的两个区间涨幅分区，不是 RRG 的相对强弱标准化。

### 下一步

- 调度任务在收盘后把每日板块快照与日线写入 PostgreSQL，页面优先读库，降低对实时接口的依赖并支持时点回看。
- 补充北向持股按板块（`RPT_MUTUAL_BOARD_HOLDRANK_WEB`）、机构调研（`RPT_ORG_SURVEYNEW`）、涨停池面板；基于入库日线做标准化 RRG 轮动图。
- 把逻辑追踪的行业假设与东财板块代码关联，在详情页直接展示对应假设与验证指标。

## 2026-10-17 INDUSTRY-2 板块入库、轮动图、涨停 / 调研面板、自选看板、假设关联

- **目标**：落实上一条「下一步」里的全部方向（用户选择：入库、自选、RRG、涨停/调研、假设关联、联调截图）。
- **自选股口径（用户确认）**：自选里只保存证券代码（本系统账号下，支持粘贴批量导入）；看板所需的行情、所属行业、资金、涨停、调研、热榜一律从公开数据现查。**不保存、不使用东财 / 同花顺登录 cookie。**
- **北向按板块不做**：`RPT_MUTUAL_BOARD_HOLDRANK_WEB` 最新 `TRADE_DATE` 停在 2024-08-16（交易所此后不再按日披露陆股通持股），做出来只会是过期数据；页面脚注说明原因。

### 变更文件

- 后端
  - 迁移 `alembic/versions/20261017_0023_board_market.py`：`board_daily_snapshots`（板块 × 交易日，唯一键 `board_code, trade_date`）与 `board_daily_bars`（secid × 交易日 OHLC / 量额）。`app/db/models/board_market.py`、`app/repositories/board_market.py`（`on_conflict_do_update` 幂等写入、按日 / 按板块读取）。
  - `app/services/board_market_sync.py` + 调度 `sync_board_market`（工作日 15:45，`dedupe_key=board_market:{day}`，最多 3 次）+ worker `board_market`：以沪深300最后一根日线定交易日；收盘后（≥15:05，或次日开盘前）写全量行业 / 概念快照；一、二级行业日线增量补齐（首次回补 420 天）；连续 3 个板块失败即停，部分失败标 `PARTIAL_SYNC` 交给重试。复用 0.4s 串行限速出口。
  - `app/services/industry_market.py`：区块新增 `origin: live|stored` 与 `data_date`；`boards?as_of=` 只读入库快照；实时失败回退最近入库快照（标 stale）；盘后且库里日线足够（≥60 根且已到最近交易日）时走势优先读库；`GET /industry-market/snapshot-dates`。
  - 轮动：`app/services/board_analytics.py` 新增 `rotation_tail`（RS = 板块 / 沪深300；RS-Ratio = 100 + 20 日滚动 z 分；RS-Momentum = 100 + RS-Ratio 变化率的 20 日 z 分；5 日一个点、8 个点尾巴）与象限；`GET /industry-market/rotation?level=1|2` 只用入库日线。
  - 情绪：`app/providers/eastmoney_sentiment.py`、`app/services/market_sentiment.py`、`app/schemas/market_sentiment.py`：`GET /industry-market/limit-up?date=`（东财涨停 / 炸板 / 跌停池；封板率、连板梯队、行业分布、首封时间分桶；非交易日向前找最多 7 天）；`GET /industry-market/org-surveys?days=1..90`（东财 `RPT_ORG_SURVEYNEW`，每次调研只取 `NUMBERNEW=1` 一行；按日、接待方式、公司聚合；前 80 家公司的行业查不到时单独标 unavailable）。
  - 自选：`app/services/watchlist_insights.py`、`app/schemas/watchlist_insights.py`、`app/repositories/watchlists.py`、`app/api/v1/watchlists.py`：`POST /watchlists/{id}/items/batch`（解析 600519 / SH600519 / 600519.SH / 1.600519，最多 300 个，回报 added / existing / missing / invalid）；`GET /watchlists/{id}/insights`（东财公开行情 → 失败改用库内日线按交易日计算；等权 / 市值加权涨跌、主力净流入合计、行业暴露；涨停池、30 日调研、同花顺热股 / 东财人气榜名次信号，任一来源失败只标 `signal_section` 缺失）。
  - 假设关联：`app/services/board_theses.py` + `GET /industry-market/boards/{code}/theses`（需登录）：板块名去掉 Ⅱ/Ⅲ 后与逻辑追踪行业同名优先、否则名称包含；返回匹配方式、当前用户在这些行业下的假设及截至今天的支持 / 挑战依据条数。
  - 测试：`tests/unit/test_board_market_store.py`（11）、`test_market_sentiment.py`（8）、`test_watchlist_insights.py`（5）、`test_board_theses.py`（2）；`tests/integration/test_board_market_repository.py`（2）、`test_watchlist_import.py`（2）。覆盖非交易时段不写快照、连续失败停止、无基准报错、实时失败回退库、外人分组 404、超量 422 等异常路径。
- 前端
  - `src/api/industryMarket.ts`（`origin` / `data_date`、`sectionBadge`、快照日期、`fetchBoardTheses`）、`src/api/marketPulse.ts`、`src/api/watchlistInsights.ts`。
  - `src/views/IndustryView.vue`：历史日期选择（入库快照）、「实时 / 收盘快照 / 缓存」角标、进入「轮动与情绪」。
  - 新页 `src/views/MarketPulseView.vue`（`/industry/pulse?tab=rotation|limit|survey`，侧栏「产业研究 › 轮动与情绪」）：`parts/pulse/RotationPanel.vue` + `rotationChart.ts`（RRG 四象限、尾巴开关、象限过滤、点选单看轨迹）、`LimitUpPanel.vue`、`SurveyPanel.vue`。
  - `src/views/IndustryBoardView.vue`：新增「研究假设」页签（`parts/industry/BoardThesesPanel.vue`），入库快照提示。
  - `src/views/WatchlistView.vue` + `parts/watchlist/WatchlistInsightsPanel.vue`：批量导入、分组看板（KPI、行业暴露 treemap、5 日 × 60 日动量散点、带信号的明细表）。
  - `src/charts/index.ts` 注册 `MarkAreaComponent`；`tests/unit/marketPulse.test.ts`（7 个用例）。

### 验证

- 后端：`ruff format --check` / `ruff check` 通过；`mypy app` 只剩 `app/trading_agents/upstream.py` 的 4 个既有错误；`pytest tests/unit` 312 passed；`pytest tests/integration`（本机 Docker `pgvector/pgvector:pg16` 测试库，端口 55432）83 passed / 1 skipped；迁移 0023 upgrade / downgrade 均验证。
- 前端：`vue-tsc --noEmit` 无错误；`vitest run` 31 个文件 / 134 个用例通过（未配置 ESLint）。
- 页面联调（2026-10-17，周六，测试库 + 本机注册账号）：
  - 涨停情绪：最近交易日 2026-10-16 涨停 61 / 炸板 49 / 跌停 10，封板率 55.5%，最高 11 连板，梯队与行业分布正常。
  - 机构调研（近 30 天）：1457 次公告、1091 家公司、13258 家次；行业分布因 push2 频控标「上游请求失败」。
  - 自选看板：粘贴 `600519 SH601318 000858.SZ 1.600036 688981 abc12` → 加入 4 只、688981 未收录、abc12 无法识别；实时行情不可用且库内无日线时整列为空并说明原因，同花顺热股名次正常显示。
  - 假设关联：为「公用事业」写一条假设后，`BK0427 公用事业` 的 theses 接口按「同名」返回该假设；未登录 401。
  - **push2 / push2his 仍对本机 IP 频控**：板块总览、板块详情返回 503，轮动图因尚无入库日线显示「收盘后任务跑过一次后才有」；未做任何绕过，未手动触发入库任务。

### 已知局限

- 轮动图与盘后读库都依赖 `sync_board_market` 至少成功跑过一次；一次全量约 170 个限速请求，worker 租约 300s 无续租，接近上限。
- 板块与逻辑追踪行业只按名称对应（申万一级 vs 东财行业），同名之外的对应关系（如东财二级「白酒Ⅱ」对申万「食品饮料」）不会自动关联。
- 板块详情页在实时行情和入库快照都没有时整页 503，「研究假设」「行业研报」页签也随之不可达。
- 自选看板的实时行情与行业字段依赖 push2；频控期间只能用库内日线，没有行业暴露。
- 涨停池行业名是东财股池原值，可能被截断（如「汽车零部」）。

### 补充：行情与快照都没有时的降级显示

- 现象：push2 限流且库里没有收盘快照时，`/industry` 整页只剩一个「实时行情暂不可用…库里也还没有收盘快照」错误框。
- 处理：`IndustryView.vue` 在这种情况下保留页面骨架，行情区块显示「暂无板块行情」，同花顺热榜照常显示，并给出板块目录；`IndustryBoardView.vue` 在行情取不到时只保留「行业研报」「研究假设」页签（新增 `parts/industry/BoardReportsPanel.vue`）。不画任何占位数字。
- 根本解决仍需 `sync_board_market` 成功跑一次（需 push2 恢复）。

### 下一步

- push2 频控解除后让 `sync_board_market` 跑一次，补 RRG、板块总览 / 详情和自选实时行情的页面截图。
- 东财行业与申万行业建立显式映射表（带版本与生效日期），替代名称匹配。

## 2026-10-17 MACRO-US 美国宏观数据扩充与指标带滑动（feat/macro-us-ticker）

设计：`docs/superpowers/specs/2026-10-17-macro-us-ticker-design.md`（子项目 1+2）。

### 变更文件

- 后端：`backend/app/catalog/macro_series.py`、`providers/eastmoney_us_macro.py`（新）、`providers/latest_revisions.py`（新）、`providers/fred.py`、`providers/fred_public.py`、`repositories/macro.py`、`services/fred_sync.py`、`services/macro_batch.py`（新）、`schemas/macro.py`、`api/v1/macro.py`、`core/config.py`、`scripts/bootstrap_data.py`。
- 前端：`frontend/src/api/macroTiles.ts`（新）、`api/macro.ts`、`views/parts/macro/MacroTicker.vue`（新）、`views/parts/macro/MacroTickerTile.vue`（新）、`views/parts/macro/tickerWheel.ts`（新）、`views/MacroView.vue`、`e2e/core.spec.ts`。
- 迁移：无（沿用 macro_series / macro_observations 现有字段）。
- 新增用例：后端 `test_eastmoney_us_macro.py`、`test_macro_batch.py`、`test_fred.py` 追加、集成 `test_us_macro_eastmoney.py`；前端 `macroTiles.test.ts`、`macroTicker.test.ts`、`tickerWheel.test.ts`、`MacroView.test.ts`、`macro.test.ts` 追加。

### 与计划不同的实现决定

- 东方财富最新值发生修订：旧版本 `realtime_end = 抓取日`（纽约日历日），新版本 `realtime_start = 抓取日`。可见性是 `realtime_start < pit <= realtime_end`，旧值看到抓取日当天，新值次日起可见；计划里的「抓取日 − 1 天」会让抓取日当天两版都看不到。
- `parse_indicator` 没有一行能解析时（含 `data: null`、空列表）报 `UPSTREAM_PAYLOAD`；同一月份多行只留发布日最晚的一行。
- 「较上期」只和紧邻上一期比（月度差 1 个月、季度差 3 个月、周度差 7 天，日度取前一个点），否则显示「—」；频率未知不算。政策利率取最近一次调整的变化，取回窗口内没有调整时显示「—」。
- 指标带不做 scroll-snap；滚轮判定是纯函数 `tickerWheel.ts`：Ctrl / 捏合缩放、横向为主的滑动、已到端点、内容不超宽都交给浏览器，按 `deltaMode` 换算像素。箭头与方向键的平滑滚动用 CSS，`prefers-reduced-motion` 时关闭。

### 验证

- 后端（`d:\finscrope\finscope-ai\backend\.venv` 解释器，在工作树 `backend` 下运行）：`ruff check .` 通过；`ruff format --check .` 305 个文件已格式化；`mypy app` 只剩 `app/trading_agents/upstream.py` 的 4 个既有错误（venv 缺 `langchain_core`，3 个 import-not-found + 1 个 subclass Any）；`pytest -q -rs` 共 429 个用例：355 passed、74 skipped、0 failed，74 个跳过全部是集成测试，原因「PostgreSQL 不可用」（本机没有可连的 PostgreSQL，含本轮新增的 `test_us_macro_eastmoney.py` 2 个用例）；`pytest tests/unit` 343 passed。
- 前端：`npm run typecheck`（vue-tsc --noEmit）无错误；`npm run test` 35 个文件 / 174 个用例通过；`npm run build` 成功（只有既有的单 chunk 超过 500 kB 提示）。
- 真实库验证（本机 Docker `pgvector/pgvector:pg16` 测试库 `finscope-testdb`，端口 55432，只对该测试库操作）：
  - 本轮新增的 `tests/integration/test_us_macro_eastmoney.py` 2 个用例在真实 PostgreSQL 上通过（修订行写入与批量接口 SQL）。完整 `pytest` 429 个用例中其余 72 个集成用例未连库，仍为跳过。
  - `bootstrap_data --only fred`：19 条序列首轮写入 17 条（ISM_MFG 70 条、ISM_SVC 71 条；FRED 各序列 23–2191 条），`FEDFUNDS`、`GDP` 因「上游连接中断」失败（临时网络问题）；随后单独补同步成功（GDP 23 条、FEDFUNDS 72 条），19 条全部入库。补同步的这两条 `realtime_start` 是当天，未做手工回拨，按规则次日才可见，所以当天页面规则解读里「增长」仍显示数据不足。
  - 启动后端（8010）与前端（5180）打开 `/macro/us`：15 格顺序与口径正确，失业率 4.20%、政策利率区间 3.75–4.00%、ISM 制造业 54.5 / 服务业 54.9，涨红跌绿；ISM 两格 `?` 提示含 `EMG00002790` / `EMG00002791` 与「最新修订」；滚轮横移、Ctrl 缩放与横向滑动放行、到端点放行、两侧箭头显隐均在真实浏览器里验证。
  - 真实浏览器发现并修复一个单测测不到的布局问题：`section.ticker` 作为 grid 子项 `min-width: auto` 被内容撑到 2908px，整页变宽、轨道不滚动，已加 `min-width: 0`（提交 7c87b4a）。
  - 没有 FRED API key，走公开 CSV 通道，其 `realtime_start` 取同步当天，按「发布日当天不可见」的规则，当天同步的 FRED 数据次日才可见（既有保守设计）。为了做页面截图核对，把测试库里当天同步的 12636 行 FRED 行 `realtime_start` 手工改到前一天（只改可见性元数据，不动数值，仅限该测试库）。ISM 用东方财富给的真实发布日，不受影响。

### 已知局限

- ISM 首次同步前的历史为东方财富最新修订值；东方财富为页面匿名接口，字段变化会以 UPSTREAM_PAYLOAD 失败并保留已入库数据。
- FRED 原生版本仍沿用 FRED 的 `下一版起点 − 1 天`，FRED 修订的观测在修订当天不可见。这是既有行为，不在本次范围。
- Playwright e2e（`core.spec.ts` 用例 04）已更新断言但未运行：本机 8000 端口上的后端不是本分支代码。
- 修订路径（首次写入、同值重复同步不写、数值变化新增版本）在真实 PostgreSQL 上用构造数据验证过；真实上游目前还没出现过修订事件，所以没有用真实修订验证过。

### 下一步

- 跑 e2e 用例 04（需本分支后端）。
- 子项目 3（通胀走势图 + 多指标对比分析，2020-01=100 指数化、双轴、导出）。

## 2026-10-17 DASHBOARD 总览驾驶舱（每日市场大况）

- **目标**：`/dashboard` 按 `docs/prototypes/dashboard/reference.png` 布局重做，覆盖全球宏观（流动性松紧、宏观趋势）、A 股温度（带计算依据）、主要指数、热点 / 领涨板块、热股 / 涨幅股、我的自选、重要 / 热门公告。按用户要求去掉参考图的「关注状态」列与「加入自选」按钮；参考图上的文字与数字不照抄，全部由真实数据计算。
- **口径**：所有分数都是纯函数规则（`app/services/dashboard_rules.py`），带版本号、权重、原始数据、换算公式与贡献；可用权重不足 50% 不出分。前端不计算任何财务数字，「较上一交易日」由后端读 `dashboard_daily_metrics` 算出。

### 变更文件

- 后端
  - 规则 `dashboard_rules.py`：market-temperature-v2、global-liquidity-v1、global-growth-v1 + 经济热度 × 通胀动能象限、board-heat-v1、自选强弱、公告分级与热度、`contributions()`（得分 × 权重 / 可用权重）。
  - 数据源：`providers/tencent_market.py`（指数 / 个股快照、日周月 K、分时）、`providers/sina_market.py`（板块与个股涨幅榜）、`providers/eastmoney_rates.py`（SHIBOR、中美国债收益率）、`eastmoney_sentiment.py` 新增全市场涨跌分布、`disclosures.py` 新增巨潮全市场检索；外盘与美国宏观复用 FRED 公开 CSV 与现有出口。`market_cache.py` 的 POST 支持表单类型；`industry_market.py` 拆出 `ths_stocks()` / `ths_plates()` 共用缓存；`watchlist_insights.py` 在东财行情失败时先改用腾讯快照，再退回库内日线。
  - 迁移 `20261017_0024_dashboard_metrics.py` + `db/models/dashboard_metric.py` + `repositories/dashboard_metrics.py`：按（指标, 日期）幂等写入每日温度、流动性、增长分和热点板块数，`previous()` 取更早的最近一天。
  - `services/cockpit.py`、`schemas/cockpit.py`、`api/v1/dashboard.py`：7 个接口见 `docs/API.md`「总览驾驶舱」；每个上游独立 `Section`，单源失败只让对应字段为空并说明原因；TTL 缓存 + 现有串行限速出口，无循环重试。
  - 测试：`tests/unit/test_dashboard_rules.py`、`test_dashboard_providers.py`（解析器用 `tests/unit/fixtures/` 下 2026-10-16 真实响应截取片段，只供测试，不进运行时）、`test_cockpit_service.py`（含贡献之和 = 总分、较上一交易日、无历史、上游失败）；`tests/integration/test_dashboard_metrics.py`（upsert 与取前一日、无历史）。
- 前端
  - `src/api/dashboard.ts`：类型、解析与 7 个请求；链接只放行 http(s)。
  - `src/views/DashboardView.vue` 重写：4 张指标卡（全球宏观、A 股温度、热点板块数、重点关注股票）+ 指数走势 / 板块 / 热股 / 宏观 / 自选 / 公告六个面板，各自加载、空、错误、过期状态；温度、宏观、板块热度三个「计算依据」弹窗。
  - `src/views/parts/dashboard/`：`IndexPanel`（分时 / 日 / 周 / 月 K、均线、成交额、两市全景）、`BoardsPanel`、`StocksPanel`、`MacroPanel`、`ScoreGauge`、`WatchPanel`（读现有自选分组，「管理自选 ›」跳 `/watchlist`，最多显示 10 只）、`AnnouncementsPanel`、`FactorTable`、`charts.ts`、`panel.css`。
  - `components/ui/StateBlock.vue`：骨架屏不再继承全局 `.skeleton` 的 180px 最小高度。
  - `tests/unit/dashboard.test.ts`（14 个用例：解析正常 / 缺失、URL 安全、K 线窗口、无昨收的分时图等）。

### 验证

- 后端：`ruff check` / `ruff format --check` 通过；改动文件 `mypy` 无错误；`pytest tests/unit` 全部通过；`tests/integration/test_dashboard_metrics.py` 在本机测试库（Docker pgvector，端口 55432）通过，迁移 0024 upgrade 验证。
- 前端：`vue-tsc --noEmit` 无错误；`vitest run` 32 个文件 / 148 个用例通过；`vite build` 成功。
- 页面联调（2026-10-17 周六，最近交易日 2026-10-16，测试库 + 本机注册账号，1680 宽）：温度 38.9「中性偏弱」，流动性 42.2「中性偏紧」，增长 46.7「中性」、象限「通胀回升」，热点板块 5 个，公告扫描 179 条，自选 7 只（强 1 / 震荡 5 / 弱 1）；全部指数与周期 K 线正常。未登录时自选面板显示登录入口。

### 已知局限

- 宏观接口首次约 13 秒（FRED 公开 CSV 逐条取），之后走缓存；市场接口首次约 6 秒。
- 东财个股行情对本机 IP 仍频控，自选改用腾讯快照，首次约 11 秒。
- 「较上一交易日」要等 `dashboard_daily_metrics` 有前一天的记录；新库第一天显示「尚无入库记录，明天起可对比」。写入发生在接口被访问时，没有单独的定时任务。
- 公告重要度只按标题关键词分级，不读正文。

### 下一步

- 给 `dashboard_daily_metrics` 加收盘后定时写入，不依赖有人打开页面。
- 宏观 FRED 序列改为读已入库的宏观数据，缩短首次加载时间。

## 2026-10-17 MACRO-US 同步区间与读取上限可配置（生产补数据中暴露的问题）

### 现象与根因

- 生产站新增的 10 条序列库里是 0 行，页面显示「暂无数据」。其中 `DFEDTARL` / `DFEDTARU` 同步失败：政策利率每个自然日都有一条，`2020-10-17` 到当天是 2192 个版本日，超过 FRED JSON 接口「一次最多 2000 个版本日」的限制，整次请求被拒，一条都没写入。美债只在交易日更新，同样的六年区间不会超限，所以能成功。
- 版本日由请求的 `realtime_start`（取观察区间起点）算到 `realtime_end`；结束日（观察日）不会缩短它，所以「把区间拆成两次」在旧实现里没有用。

### 改动

- `providers/fred.py`：`vintage_windows` 把实时区间按每段至多 2000 天切开，最后一段终点仍是 `9999-12-31`；`fetch_vintages` 逐段请求，观察区间保持完整；`merge_adjacent_vintages` 把被边界截断、值相同、首尾相接的同一版本合回一行，值不同的相邻行是真实修订，原样保留。
- `core/config.py`：新增 `MACRO_SYNC_MAX_WINDOW_DAYS`（默认 36500，范围 365–73000）取代写死的 3660 天；`MACRO_BATCH_LIMIT_DAILY` / `_WEEKLY` / `_OTHER`（默认 2500 / 600 / 240，范围 1–20000）取代写死的 1000 / 260 / 120。
- `services/macro_batch.py`、`repositories/macro.py`：批量读库按每条序列的 limit 与频率换算观察日下界（宁大勿小），库里历史存得再长，每次也只读所需的那一段。
- `scripts/bootstrap_data.py`：新增 `--fred-years`（默认 10），受区间上限约束。
- 单元测试 411 个全部通过（含切段、合并、可配置上限、读库下界）；ruff、mypy 通过。

### 没有做的事 / 限制

- 读库下界的 SQL（按序列 OR 条件）没有在真实 PostgreSQL 上跑过，只有单元测试覆盖换算；部署后要看一次 `/macro/us` 的批量接口返回。
- 批量接口的 `truncated` 在下界截掉更早历史时不会置真；页面不使用该字段。
- 数据管理页的「同步」是同步请求，区间很长时可能被反向代理超时；长区间回补请用 `bootstrap_data`。
- 数据管理一次仍只能提交一个序列代码。

## 2026-10-18 WATCH-EXT 自选支持非 A 股标的（板块、指数、美股、基金、国债收益率……）

### 背景

从东财导入自选时，分组里除沪深个股还有行业板块、全球指数、美股、国债收益率、期货、外汇、ETF。原来只能存本系统 `instruments` 里的 A 股，其余全部被丢弃或报「未收录」。

### 方案

- **按东财 secid（市场编号.代码）保存**，不往 `instruments` 里塞假证券：`instruments.market` 枚举只有 CN / US、`type` 只有 STOCK / INDEX / ETF，塞进去会污染证券检索、行情入库和统计。外部标的的行情、名称一律由东财现查。
- 本系统已收录的（A 股个股、已收录的美股）仍然挂到 `instruments`，公司页照常可点；匹配不上才落成外部标的。
- 外部标的**不进入 A 股汇总**：涨跌家数、等权 / 市值加权涨跌、行业暴露、涨停 / 调研 / 热榜信号都只算本系统收录的沪深京个股，避免把国债收益率、美股、板块的涨跌混进去。它们在看板里单独列成「其他标的」。

### 变更

- 迁移 `20261018_0026`：`watchlist_items.instrument_id` 改可空，新增 `ext_secid`、`ext_name`；`CHECK (num_nonnulls(instrument_id, ext_secid) = 1)`；`(group_id, ext_secid)` 部分唯一索引。降级会删除外部标的行。
- `app/utils/watch_secid.py`（新）：secid 解析、类别（A股 / 基金 / 债券 / 板块 / 指数 / 外汇 / 期货 / 国债收益率 / 美股 / 港股 / 其他）、展示名。未列入的市场编号归「其他」，不猜。
- `providers/eastmoney_boards.py`：`parse_secid_quotes` 按 `市场.代码` 建键（A 股 000660 与韩股 000660 同码不同市场）。
- `repositories/watchlists.py`：`add_external`（去重、已收录美股改挂 `instruments`）；`remove_item` 同时接受条目 id 与证券 id；`_group` 批量取证券，不再逐行查询；`ImportOutcome.missing_keys` 保留交易所。
- `services/watchlist_insights.py`：`parse_targets` 取代 `parse_codes`（`1.` / `0.` 前缀按沪 / 深京处理，所以 `1.000001` 是上证指数而不是深市平安银行；无前缀的 5xxxxx / 15xxxx / 16xxxx 基金按号段推断）；`_others` 现查外部标的行情；`resolve_names` 导入时取名称，东财不可用则照存、之后看板里现查。
- API：`POST /watchlists/{id}/items/batch` 改为先匹配本系统、匹配不上再按东财代码保存，返回 `added / existing / external / unnamed / invalid`（去掉 `missing`）；`GET /watchlists` 的条目新增 `item_id / secid / kind / kind_label`，`instrument_id` 可为空；`DELETE /watchlists/{id}/items/{item_ref}` 的 `item_ref` 为条目 id（兼容证券 id）；看板返回 `others / others_section`（去掉 `unsupported`）。
- 前端：自选列表给外部标的打类别标签、不请求日线、板块可跳行业板块页；看板新增「其他标的」卡片（按类别筛选、涨跌条形图、明细表）；批量导入提示说明哪些是单独保存的。

### 验证

- 后端：新增 `test_watch_secid.py`（类别 / 展示名 / 同码不同市场）、`test_watchlist_insights.py`（解析、其他标的单列且不进汇总、东财失败时降级、名称解析）、`test_watchlist_import.py`（外部标的保存 / 去重 / 越权 / 按条目 id 移出、美股挂靠已收录证券）。自选相关测试在本机 pgvector 容器上全部通过；迁移在真库上升级 → 降级 → 再升级通过。
- 前端：`marketPulse.test.ts` 更新并新增旧后端兼容用例；216 个测试通过，`vue-tsc` 通过。
- 全量后端测试里仍有 1 个失败：`test_board_data_flow.py::test_macro_china_reports_real_stored_counts`，来自工作区里尚未提交的中国宏观改动（序列数量变化），与自选无关。

### 已知局限

- 外部标的没有入库日线，只有东财实时 / 最近一个交易日行情；东财不可用时这部分显示「行情不可用」，不用别的来源补。
- 国债收益率的「涨跌」是收益率自身的变化幅度，不是价格涨跌；已在界面说明。
- 东财查不到的代码（退市或写错）照存、名称显示为代码，并在导入结果的 `unnamed` 里列出。
- 总驾驶舱的「我的重点关注」仍只展示 A 股，外部标的不出现在那里。
- 不会自动同步东财自选，需要时重新导入。

### 追加：名称补全

- 本机导入 423 个标的时发现，东财对同一批请求偶尔少回几行，导致一部分外部标的没取到名称。`resolve_names` 对缺失的代码补查一次；看板成功取到名称后，`WatchlistRepository.fill_external_names` 把名称回填到库里（只补空名称，不覆盖已有，限本人分组），列表页不再一直显示代码。
- 用与后端相同的参数在浏览器里复现过：423 个里随机取 100 / 60 / 30 个，东财都全量返回，所以少回行是偶发，不是请求参数问题。本机直连 `push2.eastmoney.com` 此时被对端断开（`上游连接中断`），生产服务器不受影响。
- 逆向分析另见 `docs/research/东方财富账号绑定逆向分析.md`（只读分析，无代码改动）。

## 2026-10-18 MACRO-CN 中国经济页：20 格滚动指标带与数据口径对齐

### 目标

按参考图把 `/macro/china` 顶部改成与美国页同一套滚动指标带（`MacroTicker`），固定 20 格、顺序由用户指定；数据接口与美国页对齐（批量 `/api/v1/macro/observations`）。本轮只做数据层与指标带，通融走势 / 宏观象限 / AI 解读 / 对比分析 / 情景推演不在范围内。

### 改动

后端：
- `services/china_macro.py`：目录新增 17 条序列；`STALE_DAYS_BY_FREQUENCY`（日 14 / 周 21 / 月 90 / 季 215 天）；`CsvSeriesSpec.manual_only`。
- `providers/nbs_macro.py`（新）：国家统计局数据发布库 esData 适配器，覆盖核心 CPI、PMI 新订单 / 新出口订单、工业企业利润、民间固投、制造业固投、商品房销售额、一般公共预算支出、GDP 当季同比；按 `i_showname` 校验指标名，不一致按 `BAD_PAYLOAD` 拒收；只带 UA 头，请求间隔 1.5 秒。核心 CPI、GDP 来源是指数（=100），入库时减 100。
- `providers/china_macro_sources.py`：东方财富新增 M1、社零、出口；公布日保守上界新增 `next_month_28`、`quarter_next_month_20`。
- `services/data_sync.py`：`sync_china_macro` 追加 NBS 请求，失败按请求汇总，仍以 `UPSTREAM_PARTIAL` 报错。
- `services/macro_batch.py`：批量接口接受中国序列代码，读库下界按序列频率换算。
- `api/v1/research_board.py`：`/macro/china` 序列新增 `frequency`、`source_agency`、`manual_only`，CSV 提示说明哪些序列只能手工导入。

前端：
- `api/chinaTiles.ts`（新）：20 格定义与 `buildChinaTiles`；无数据显示「—」，提示里区分「手工导入」与「等待首次同步」。
- `api/macroTiles.ts`：`TileSpec.note`、`TileCatalogItem.sourceNote`，`tileInfo` 追加口径与来源（美国页输出不变）。
- `views/parts/macro/MacroTicker.vue`：新增 `label`（默认「美国宏观指标」）。
- `views/MacroChinaView.vue`：`MetricTile` 网格换成 20 格 `MacroTicker`；指标带取数失败只显示该区域错误并可重试；陈旧提示改为按频率；来源文案加入国家统计局、海关总署。

### 测试

- 后端单元：`test_nbs_macro.py`（指数换算、两个 PMI 指标、季度、名称不符、坏值跳过、坏载荷、请求体、目录覆盖）、`test_china_macro_sources.py`（目录 / 规则覆盖、手工导入集合、季度与利润公布日）。
- 前端：`chinaTiles.test.ts`、`MacroChinaView.test.ts`（20 格、单次批量请求且不超 20 个代码、手工指标显示「—」、指标带失败、整页失败）、`macroTicker.test.ts` 新增 label 用例。
- 结果：前端 204 个测试通过，`typecheck`、`build` 通过；后端单元测试通过，`ruff` 通过；`mypy` 仅 `trading_agents/upstream.py` 4 条既有错误（缺 langchain 类型桩，与本次无关）。
- 联网核验：用真实适配器对 15 条自动来源逐条取数，全部有值（核心 CPI 1.0、PMI 50.1、PPI 5.0、M1 3.2、出口 36.3、GDP 4.3 等，具体以库内为准）。

### BLOCKED / 没有做的事

以下 5 个指标没有找到可核实的公开自动来源，目录里标记 `manual_only`，页面显示「暂无数据」，需管理员导入官方口径 CSV，**不编造数值**：
- 社融存量同比增速、企业中长期贷款增量、社融中政府债券净融资额、央行 7 天逆回购操作利率：人民银行官网对非浏览器请求返回 403，不绕过；商务部只有社融增量且停在 2026-04。
- DR007：中国货币网只有 FDR007 定盘利率，不是 DR007 加权平均，不能替代。

口径说明：
- GDP 取国家统计局「当季同比」，不是累计同比；观测日取季首日。
- 一般公共预算支出取国家统计局「国家财政支出（不含债务还本）」累计同比，未与财政部逐月核对。
- 累计同比类指标 1—2 月合并公布，没有 1 月值，页面不跨月算变化。
- 核心 CPI 历史只到 2021 年；国家统计局目录 ID 按年份分段（2026- / 2021-2025），换年需要补新的 cid，指标名校验会在不匹配时报错而不是写错数。
- PMI 等未做修订版本追踪。
- `tests/integration/test_china_macro_sync.py` 已更新，但本机没有 PostgreSQL，未能运行；部署后需要在真实库上跑一次并同步一次。
- 参考图的其他面板未做；其中「50% / 30% / 20%」情景概率没有来源，后续只能用有出处的统计方法替代。

## 2026-10-18 MACRO-CN 中国经济页：宏观象限、走势图、规则解读

### 改动（仅前端，无新接口、无新数据源）

- `api/chinaPanels.ts`（新）：纯函数。走势图可选 9 个同比口径指标；`buildQuadrant`（横轴实际 GDP 当季同比、纵轴核心 CPI 同比取该季最后一个月，分界线 = 最近 3 年窗口内两轴各自的中位数，配对季度少于 4 个不定位）；`buildReadout`（增长 / 通胀 / 景气 / 货币四个维度，只比较最近两期已公布值的方向，PMI 对 50 荣枯线）；`chunkCodes`（按批量上限 20 分块）。
- `views/MacroChinaView.vue`：指标带下方新增三栏（象限 + 当前位置、通胀走势与增长货币走势两张可选指标图、规则解读）。页面序列 = 指标带 20 个 + CPI、M2，分两次批量请求；原「指标走势 / 指标清单」保留在下方。

### 口径与限制

- 象限分界是历史中位数，不是官方目标；GDP 季度数据最新一季出得晚，所以最新点是已公布的最新季度。
- 缺核心 CPI 的季度直接跳过，不用邻月替代；核心 CPI 历史从 2021 年起，3 年窗口内配对季度数以实际数据为准。
- 规则解读不给概率；没有数据的指标（含 5 个手工导入指标）只列出、不参与判断、不估算。货币维度只含 M1 / M2，不含 DR007、逆回购利率、社融。
- 没有做：多指标对比分析（2020-01=100 指数化）、AI 宏观解读、情景推演（概率须改为历史统计口径）。美国页同样尚未做这三项。
- 测试：`chinaPanels.test.ts`、`MacroChinaView.test.ts`（新增象限 / 解读用例与数据不足用例，批量请求改为两次）；前端 215 个测试通过，typecheck、build 通过。未在浏览器里目视检查布局（本机无 PostgreSQL，起不了后端）。

## 2026-10-18 MACRO-CN / MACRO-US 对比分析、AI 宏观解读、情景推演；5 个手工指标的来源复核

### 改动

后端：
- `services/macro_interpretation.py`（新）+ `POST /api/v1/macro/interpretation`（需登录）：读库（与批量观测接口同一套可见性）→ 生成「资料」行 → 模型转述 → 逐个数字核对（`49.4` 与 `49.40` 视为同一个数；1–9 个位数不查）→ 通过才返回文字。未配模型、核对不过、模型失败、无数据各有独立 `status`，核对不过的文字不返回。1 小时缓存，同一用户最多 2 个并发模型任务。`chat_model.complete_chat` 新增可选 `system` 参数，默认行为不变。
- 测试：`tests/unit/test_macro_interpretation.py`（9 个：资料口径、美国 YoY / 折年率、未公布不可见、未配模型、通过并缓存、缓存过期、含未知数字被拒、模型失败不泄露异常、数字核对边界）。

前端：
- `api/macroStats.ts`（新）：象限与情景推演的通用统计（分位数、季度配对、象限模型、象限转移次数、季度变化量分位区间）；`chinaPanels.ts` 的象限改为调用它。
- `api/macroCompare.ts`（新）+ `parts/macro/MacroComparePanel.vue`：对比分析。原值（左右双轴，最多两种单位）与指数化（2020-01 = 100，只对水平值）；不可指数化 / 缺基期的序列写明原因而不硬算；CSV 导出（日期并集，缺观测留空）。美国页默认指数化（CPI、核心 PCE 价格指数、平均时薪、非农就业人数），中国页所有序列都是同比或景气指数，指数化按钮禁用。
- `parts/macro/MacroScenarioPanel.vue`：未来宏观行情推演，统计口径。当前象限下历史上一个季度的转移次数 k / n（n < 5 只给次数）；下一季度变化量 10% / 50% / 90% 分位区间（样本 < 8 不给）。参考图里的 50% / 30% / 20% 没有采用。
- `parts/macro/MacroAiPanel.vue`：点击才请求模型；未登录提示登录；`ok` 才显示模型文字并标注模型名；其他状态只显示资料。
- 两个页面（`MacroView.vue`、`MacroChinaView.vue`）都挂上三块；美国页情景推演用实际 GDP 环比折年率 × CPI 同比。
- 测试：`macroStats` / `macroCompare` / `macroPanels` 新增 3 个测试文件，两个页面测试补充面板断言；前端 239 个测试通过，typecheck、build 通过。

### 5 个手工导入指标：来源复核结果（本轮没有新增自动来源）

重新逐个探测，结论仍是不能合规自动取数，不编造、不替代：
- 中国人民银行官网（社融存量、企业中长期贷款、逆回购操作利率）：对非浏览器请求返回 403，不绕过。
- 财政部官网：`/zhengwuxinxi/caizhengshuju/` 返回 302，没有可用的公开数据接口；地方政府债券信息公开平台（celma.org.cn）只含地方债，不是社融口径的「政府债券净融资」（含国债）。
- 中国货币网：`fdr-chrt.csv` / `frr-chrt.csv` 是 FDR / FR 定盘利率且无表头，列含义无法核实，且不是 DR007 加权平均利率，没有接入。
- 东方财富数据中心：`RPT_ECONOMY_RMB_LOAN` 只有新增人民币贷款合计，不是企业中长期贷款；没有社融存量、DR007、逆回购报表。
- 国家统计局数据发布库：目录接口本机探测返回「服务异常」，且没有这几项。
这 5 个指标继续显示「暂无数据」，由管理员导入人民银行官方口径 CSV（`POST /api/v1/macro/china/observations`）。

### 限制

- 路由 `POST /macro/interpretation` 没有集成测试（本机没有 PostgreSQL），服务层有单元测试；上线后需用真实模型试一次。
- 页面没有在浏览器里目视检查；组件测试里图表是桩。
- 情景推演的样本很小（中国核心 CPI 自 2021 年起，约 20 个季度），页面会写明样本区间和样本数，样本不足时只给次数。
- 中国页的象限分界在象限图（近 3 年）与情景推演（全部历史）里不同，页面上分别标明。


## 2026-10-18 MACRO-CN 人民银行统计表：三项指标改为月度手工导入，两项无数据指标丢弃

### 结论

- 人民银行官网 `robots.txt` 是 `User-agent: * Disallow: /`（只放行 Baiduspider），所以**没有写自动抓取脚本**，也没有写用浏览器请求头绕过的代码；这与 AGENTS.md「不得绕过网站限制」以及先前约定的「遵守 robots.txt」一致。
- 数据是在你同意后，用 Cursor 内置浏览器以人工节奏一次性读取公开统计页（约 40 次页面请求，间隔 2.5 秒以上，只读 `调查统计司` 的公开统计表和数据解读）。读到约 40 次时，公开市场业务公告的详情页开始返回 403（访问控制），**当场停止，没有重试或换方式**。
- 因此：7 天逆回购利率没有读到（公告详情页被拒）、DR007 在人民银行 / 中国货币网都没有可核实的口径，这两项按「查不到的丢弃」处理。

### 改动

- 新增 `backend/seed_data/china_pboc_monthly.csv`（60 行，2025-02 至 2026-09，公布日取人民银行官方发布日期）：
  - 社融存量同比：`社会融资规模存量统计表` 的增速列，原值照录。
  - 政府债券净融资：`社会融资规模增量统计表` 的政府债券列（亿元），原值照录。
  - 企业中长期贷款增量：`金融机构人民币信贷收支表` 里企（事）业单位中长期贷款余额的逐月差分（亿元）。官方只在文字里给累计增量，统计表里是余额，所以这一项是**差分得到的**，不是官方直接给出的月度增量。
  - 2025-01 缺失：官方列表里没有对应的单月报告，公布日期不明，不导入。
- 核对：2026 年 1–9 月政府债券合计 10.31 万亿，对应报告「政府债券净融资 10.3 万亿元」；企业中长期贷款差分合计 6.5292 万亿，对应报告「中长期贷款增加 6.53 万亿元」；社融存量同比 2026-09 为 7.1%，与报告一致。
- 新增 `app/scripts/import_china_csv.py`：`python -m app.scripts.import_china_csv seed_data/china_pboc_monthly.csv`，与管理员导入接口共用校验与幂等写入，不合规行逐条列出。**本机没有 PostgreSQL，这个脚本的入库部分没有运行过**，只测了读取与校验。
- 目录里去掉 `OMO_7D_CN`、`DR007_CN`；指标带由 20 格变为 18 格（顺序不变）；页面序列 18 + CPI、M2 = 20，一次批量请求即可。
- 提示文案改为「来自中国人民银行调查统计司统计表，不做自动抓取，由管理员按月导入 CSV」。

### 已知局限

- 新数据要每月手工导入一次（人民银行每月 12–15 日前后发布）；没导入时指标带仍然显示「—」。
- 统计表里的历史月份取自最新一期发布，早期月份若被官方修订，这里是修订后的值，公布日仍记首次发布日。
- 这三项在数据库里还没有导入，页面要执行导入脚本后才会显示数值。

### 验证

- 后端：`test_china_pboc_seed.py`（种子文件通过与管理员导入相同的校验；与官方文字口径核对；已丢弃序列与坏行被拒；文件缺失 / 无法解码只报错不抛异常）、`test_china_macro_sources.py` 更新；`pytest tests/unit` 全部通过，ruff 通过，mypy 仅剩原有的 `trading_agents/upstream.py` 4 个错误。
- 前端：`chinaTiles.test.ts`、`chinaPanels.test.ts`、`MacroChinaView.test.ts` 更新为 18 格并保留「手工指标无数据显示 —」用例；241 个测试、typecheck、build 通过。

### 追加：数据中心的月度导入提醒与官方链接

- 数据中心（`/data-center`，仅管理员）新增「中国宏观 · 人民银行月度导入」卡片（`views/parts/data/ChinaImportPanel.vue`，逻辑在 `api/chinaImport.ts`）：
  - 读取 `GET /api/v1/macro/china`，对三项序列（社融存量同比、政府债券净融资、企业中长期贷款）逐项判断是否落后：北京时间每月 16 日起应有上月数据，15 日及以前应有上上月（人民银行实际发布日在 12–15 日）。落后显示「待导入：最新 X，应有 Y」，没有数据显示「暂无数据，待导入」，顶部给出黄色提醒。
  - 给出人民银行调查统计司「数据解读」「统计数据」两个官方页面链接（新标签页打开，`rel="noopener noreferrer"`）。链接只用于管理员在自己的浏览器里打开核对，系统不会替用户请求这些页面。
  - 提供 CSV 选择与「导入」按钮，调用已有的 `POST /api/v1/macro/china/observations`；显示入库 / 重复条数，不合规行最多列 5 条；导入成功后重新读取状态。
  - 读取状态失败时仍显示提醒与链接，不假装数据是最新。
- 测试：`chinaImport.test.ts`（月份推算含跨年与北京时区、缺失与坏格式按缺失）、`ChinaImportPanel.test.ts`（落后提醒与链接、全部最新、读取失败、导入成功并刷新、未选文件 / 空文件 / 接口拒绝）；前端 252 个测试、typecheck、build 通过。
- 限制：没有在浏览器里目视检查该卡片（本机无 PostgreSQL，起不了后端）；提醒只在管理员打开数据中心时显示，没有邮件或站内通知。

## 2026-10-18 EM-BIND 东方财富扫码绑定与自选自动同步

### 背景

逆向分析（`docs/research/东方财富账号绑定逆向分析.md`）已用真实扫码验证：东财二维码登录会在成功响应的 `Set-Cookie` 里下发 `ct / ut / pi / uidal` 等登录 cookie（约 90 天），带上它即可读取全部自选分组。本轮把这条链路做进系统：在「自选研究」页扫码绑定，之后自选自动与东财保持一致，不再手动导入。

### 方案

- **单向同步：东财 → 本系统。** 不往东财写任何东西。
- 分组按东财分组 id（`em_gid`）匹配；本系统里已有同名且没绑定的分组会被认领，不重复建；东财里改名后下次同步跟着改名。
- 条目带来源 `source = manual | eastmoney`。同步只会移除 `eastmoney` 来源且东财里已删掉的条目，**手动加的条目永不被同步删除**。
- 失败不丢数据：东财登录失效、上游出错、返回空列表都不会删任何条目；登录失效时绑定状态改为 `expired`，页面提示重新扫码。
- 登录 cookie 用 `SecretBox`（Fernet，主密钥为 `SETTINGS_ENCRYPTION_KEY`，空则由 `JWT_SECRET` 派生）加密后存库；接口与日志里没有 cookie。解除绑定立即删除凭证。东财没有服务端登出接口，已颁发的登录态到期前仍有效（可在东财 App 里退出登录）。
- 二维码会话放进程内（单进程 uvicorn，有 50 条上限，2 分钟过期，同一用户 3 秒内不重复生成）。后端每 300 秒（`EASTMONEY_SYNC_INTERVAL_SECONDS`）为所有已绑定用户同步一次，页面打开时前端每 60 秒触发一次；同一用户 30 秒内重复请求直接返回上次结果。`EASTMONEY_SYNC_ENABLED=false` 可关闭后台同步。
- 东财里的板块、指数、美股、ETF、国债收益率等沿用 WATCH-EXT 的外部标的存储（`ext_secid`），不进入 A 股汇总。

### 变更

- 迁移 `20261018_0027_eastmoney_binding.py`：新表 `eastmoney_bindings`（每用户一行，加密凭证、昵称、状态、最近同步信息）、`watchlist_groups.em_gid`（含 `(owner, em_gid)` 部分唯一索引）、`watchlist_items.source`。降级可逆（已验证升级 → 降级 → 再升级）。
- 后端：`providers/eastmoney_account.py`（二维码、轮询、读取分组）、`repositories/eastmoney_bindings.py`、`services/eastmoney_binding.py`、`api/v1/eastmoney.py`（5 个接口，见 `docs/API.md`）、`repositories/watchlists.py`（`ensure_eastmoney_group`、`group_ext_secids`、`remove_stale_synced`，`add_codes / add_external` 增加 `source`）、`main.py` 生命周期里的后台同步任务、`core/config.py` 两个开关。
- 前端：`src/api/eastmoneyBinding.ts`；`views/parts/watchlist/EastmoneyBindPanel.vue`（未绑定 → 扫码弹窗，倒计时、过期重生成；已绑定 → 昵称、最近同步、立即同步、重新扫码、解除绑定；登录失效提示）；`WatchlistView.vue` 接入，同步后静默刷新分组（不切回加载态，扫码弹窗不会被卸载）。
- 测试：`tests/unit/test_eastmoney_account.py`、`tests/integration/test_eastmoney_binding.py`（真实 PostgreSQL：认领同名分组、改名、只删 eastmoney 来源、失败/空列表不删、登录失效、节流）、`tests/integration/test_eastmoney_api.py`（扫码 → 同步 → 解绑整条链路、越权）、前端 `tests/unit/eastmoneyBinding.test.ts`（9 个）。

### 验证

- 后端：`ruff check` / `ruff format --check` 通过；`mypy app` 仅剩 `trading_agents/upstream.py` 的 4 个既有错误（venv 缺 `langchain_core`）；`pytest` 554 个用例，551 通过、1 跳过、2 失败——失败的是 `test_dashboard_metrics.py` 两例，原因是本机测试库里残留的驾驶舱历史数据，与本轮无关（新建干净库不受影响）。
- 前端：`vue-tsc --noEmit` 无错误；`vitest run` 38 个文件 / 202 个用例通过。
- 真实上游：用用户本人的东财 App 扫码，完整走通二维码生成 → 轮询 → 登录成功 → 服务端 cookie 读取 7 个分组共 423 个成员（见研究文档第 8 节）。

### 已知局限

- 同步方向只有东财 → 本系统；在本系统里改东财自选不会回写。
- 在东财里把标的从分组删掉，下次同步时本系统里对应的 `eastmoney` 来源条目才会消失；同步来的条目在本系统手动移出后，下一次同步会再加回来。
- 二维码会话在进程内，多副本部署或重启会让进行中的扫码作废（重新扫即可）。
- 登录态到期（约 90 天）或在东财被踢下线时需要重新扫码；没有服务端登出接口。
- 首次同步会新建「自选股」等分组；生产上已有的手动导入分组「东方财富自选」（352 只 A 股）不会被认领（名称不同），会与新分组内容重叠，需要时手动删除旧分组。
- 页面关闭后只有后端 300 秒一次的后台同步在跑；东财限流时同步失败只会记录在绑定状态里。


## 2026-10-18 CUSTOM-ANALYSIS 自定义数据分析：期权 PCR、ERP、巴菲特指标、中位数 PB

### 背景

在侧栏「AI投研助手」下面加「自定义数据分析」，放研究者自己看的宏观 / 市场指标。需求文档 `a_prd.md` 给了四项：四大宽基期权 PCR、股债收益差 ERP、巴菲特指标、全市场等权 / 中位数 PB。要求数据源质量和准确性有保证。落地前逐项核验了数据源，几处与 PRD 不同（详见 `docs/superpowers/specs/2026-10-18-custom-analysis-design.md`）。

### 结论

- 全部四项做完，回填口径为 5 年。
- 数据源全部换成官方公开页面：PCR 来自上交所 / 深交所 / 中金所的每日统计，指数收盘与 PE-TTM 来自中证指数公司，10 年国债收益率、GDP、全市场截面来自东方财富数据中心。
- **中证1000 用中金所 MO 股指期权**。联网核实：512100 只在上交所模拟环境，159845 没有期权，没有可用的 ETF 期权。MO 2022-07-22 上市，不足 5 年。
- PCR 由我们用原始成交量 / 持仓量自己算，交易所披露的比值（上交所 `CP_RATE`、深交所 `rcrpccb`）只用来对账。

### 变更

后端：

- 迁移 `20261018_0028`：`option_daily_stats`、`custom_series_points`，降级可逆。
- 数据源适配器（纯 URL 构造 + 解析，经 `ProviderError`）：`providers/exchange_options.py`、`csindex.py`、`eastmoney_valuation.py`；`eastmoney_rates.treasury_url` 增加 `page_number`（默认值不变）。
- `services/custom_analysis_rules.py`：全部公式与质量闸门，纯函数、可离线测试（PCR、篮子加总、分位、通道、ERP、单季 GDP、滚动 4 季 GDP、巴菲特指标、中位数 PB、各闸门）。
- `services/custom_analysis_sync.py`：增量同步；`services/custom_analysis.py`：读取侧与 CSV；`repositories/custom_analysis.py`；`schemas/custom_analysis.py`；`api/v1/custom_analysis.py`（前缀 `/custom-analysis`，公开）。
- 调度器：工作日 18:30 的 `custom_analysis` 任务（开关 `SCHEDULER_ENABLE_CUSTOM_ANALYSIS`）；worker 新增 `custom_analysis` 任务类型，有失败段时以 `PARTIAL_SYNC` 失败。
- CLI：`python -m app.scripts.backfill_custom_analysis`（可中断、可重复）。

前端：

- 侧栏新增「自定义数据分析」（`/custom-analysis`，在「AI投研助手」之后）；`views/CustomAnalysisView.vue` 三个分组（期权 PCR / 估值水位 / 数据源与质量，`?tab=` 记住位置）。
- `api/customAnalysis.ts`（运行时收窄的解析器，缺失字段保持 null）、`views/parts/custom/` 下的三个面板与图表配置 `customCharts.ts`。每处都有 loading / empty / error / stale 状态。

### 关键口径与防护

- ETF 与股指期权分开展示不混算；篮子里任一成员当天缺数据，这一天整天不计入并计数展示，不拿单边数据冒充。
- 分位 = 最近 5 个自然年窗口内不高于当前值的占比，窗口不足 750 个交易日不给分位与通道。
- GDP 在报告期末 + 21 天后才视为已公布，先差分成单季再滚动加总，不插值，无前视。
- 中位数 PB 剔除 PB 缺失或 ≤ 0、ST、上市不满 180 天；历史 ST 按当前简称近似，同时存「不剔除 ST」对照线，并在每个点上记 `st_basis`。
- 质量闸门：期权行负数 / 全零拒收，与交易所披露比值对不上标待审；PE (0,200]、收益率 (0,15] 越界拒收；截面不足 3000 只或与声明总数不符整天作废；逐日跳变标待审但不改值。

### 联网核验中发现并处理的上游问题

- **中证指数 `index-perf`** 在 `startDate` 不是交易日时会回显一条同日期的幽灵行（周六也有），会污染交易日历。请求起点前退 7 天，只保留起点之后且不是周末的行；有回归测试。
- **东方财富数据中心单页最多 500 行**，`pageSize` 再大也不生效。首次回填国债收益率原先只取到 467 条，改为翻页直到覆盖 5 年窗口（现 1402 条）；有回归测试。
- **上交所 `TRADE_DATE` 参数无效**（永远返回最新一天），改用 `tradeDate=YYYYMMDD` 并校验返回日期。
- **深交所对连续请求会直接断连**（限流）：0.4 秒间隔约 60 个请求后开始，1.5 秒间隔同样被封，封一段时间。这是对方的限流，**没有绕过**（不换 UA、不走代理）：深交所请求之间多等 3 秒，每个请求最多重试 3 次（退避 4s / 8s），连续 3 个交易日失败就停，下次续跑。

### 验证

- 后端：`ruff check` / `ruff format --check` 通过；`mypy app` 仅剩 `trading_agents/upstream.py` 的 4 个既有错误（venv 缺 `langchain_core`）；`pytest tests` 689 通过、1 跳过（既有，`test_simulation_run` 缺 600519.SH 日线）。其中新增 110 个：规则 / 解析器 / 同步（增量、重试退避、幽灵行、翻页、深交所限速、连续失败即停、404 当无数据）/ 服务（过期、缺数据、分位门槛）/ API（含空库、未知分组、CSV）/ 真实 PostgreSQL 上的仓库层（upsert 幂等、拒收不入库、空读取）。
- 迁移在本机 PostgreSQL 上做了 `downgrade 20261018_0027` → `upgrade head` 往返。
- 前端：`vue-tsc --noEmit` 通过；`vitest` 285 通过（新增 24 个：解析器、图表配置、页面的正常 / 空 / 错误 / 过期 / 重试）。
- 真实数据（本机临时库 `finscope_custom`，没有碰主库）：
  - 上交所 1205 行自算成交量 PCR 与官方 `CP_RATE` 逐行对账，最大偏差 0.005 个百分点（官方只披露两位小数），0 行待审。
  - 2026-10-16 的读数：沪深300 PE-TTM 12.88、10 年国债 1.648% → ERP 6.12 个百分点；A 股总市值 120.17 万亿 / 滚动 4 季 GDP 143.77 万亿 = 83.6%（5577 只）；中位数 PB 2.49（不剔除 ST 2.50）。
  - 在浏览器里（本地 vite + uvicorn）看过三个分组与导航位置；缺深交所数据的沪深300 ETF 篮子如实显示「已过期、仅 1 个可用交易日」。

### 已知局限

- **深交所限流导致历史回填需要分多次完成**。没补齐时，沪深300 / 中证500 的 ETF 篮子只显示两边都有数据的那些天，并标过期；上证50、股指期权篮子不受影响。
- 10 年国债收益率没有与中债网逐日对账（中债网没有可用的公开接口）。
- GDP 取最新（含修订）值，历史读数与当时公布值会有小差异。
- 历史日期的 ST 剔除是按当前简称的近似，上限由对照线给出。
- 中证1000 的 MO 不足 5 年；不足 750 个交易日时页面不给分位并写明样本数。
- PCR 图上的「指数收盘」是跟踪指数的收盘，不是 ETF 收盘价。
- 全市场截面每次同步最多补 60 个交易日，首次回填用 CLI 一次补完。
### 补充：加入科创50 期权 PCR

用户指出科创50 也有期权 PCR，补成第五个分组 star50。

- 联网核实（上交所通知 上证发〔2023〕90号、合约条款页）：科创50 ETF 期权只在上交所，华夏 588000 与易方达 588080 两只，2023-06-05 同日上市；中金所的科创板股指期货期权仍在研发（2026-10-15 公开表态），深交所没有。因此科创50 只有 ETF 篮子（两只加总后再相除），没有股指期权篮子，页面只有一张卡，说明写在篮子备注里。
- 跟踪指数 000688（上证科创板50成份指数），中证指数 index-perf 实测可用（2026-10-16 收盘 1358.65，PE-TTM 63.3）；INDEX_CODES 自动带上，同步任务会一并拉取。
- 易方达 588080 在 2025-10-17 除息，合约单位 10000 → 10097；PCR 用成交张数，不受影响。
- 数据本来就在：上交所接口返回全部标的，这两只此前已入库（同样与官方 CP_RATE 对账，0 行待审），只是没有分组使用。
- 代码：custom_analysis_rules.OPTION_GROUPS 加 star50；前端 PCR_GROUPS 加「科创50」；接口分组校验正则本来就放行。
- 测试：规则（两只加总、缺一只整天丢弃、其他标的不混入）、服务（正常 / 缺一只降级 / 无股指期权篮子）、API（含 CSV：kind=index 返回 404）、前端（标签切换请求 star50、只有一张卡）。后端 pytest tests 693 通过、1 跳过；前端 itest 286 通过；ue-tsc、
uff 通过。
- 局限：上市不足 5 年（约 3.4 年），样本不足 750 个交易日前不给分位。
## 2026-10-18 CHINA-MACRO-CATCHUP 中国宏观新增序列部署后为空：启动补跑与种子文件进镜像

### 现象与定位

宏观页指标带里社融存量同比、PMI 新订单、工业企业利润增速、M1 同比显示「暂无数据」。读线上 GET /api/v1/macro/china：只有最早的 6 个序列有数据，后加的 13 个条数都是 0，人民银行三项也是 0。

用真实上游逐个实测（只读、不写库）：M1、PMI 新订单、PMI 新出口订单、工业企业利润、民间 / 制造业投资、商品房销售、财政支出、GDP、出口全部取得到最新数据（2026-09 / 2026-08），抓取逻辑没有问题。原因是两个：

1. 定时任务每天 01:20 才登记一次，部署完当天新增序列没有数据。
2. 人民银行三项是人工导入序列，已核对的种子 backend/seed_data/china_pboc_monthly.csv 没有进后端镜像，线上容器里没有这个文件，没法用脚本导入。

### 变更

- services/scheduler.py：china_macro_missing_series（纯函数：自动序列里一条都没有的，人工序列不算）与 catch_up_china_macro；workers/scheduler.py 启动时调用，失败只记日志不拦调度器。登记键按天幂等，同一天重启多次只有一条。
- backend/Dockerfile：COPY seed_data ./seed_data。
- docs/使用说明.md 6.2：补「刚部署完怎么没数据」与人民银行三项的导入命令。

### 没有做的事

没有去爬人民银行网站：对方对普通请求直接返回 403，robots.txt 不允许抓取，项目规则不绕过站点限制。社融存量同比继续走已核对的 CSV 导入。

### 验证

- 新增 5 个单测：缺序列时登记一次、全部有数据不登记、只缺人工序列不登记、开关关闭不登记、缺失清单只含自动序列。
- 本机临时库端到端：补登记 1 次（同一天再调 0 次）→ sync_china_macro 写入 1863 条 → 导入种子 60 条，自动序列无空缺。M1 同比 3.2%、社融存量同比 7.1%（2026-09）与人民银行 10 月 14 日公布值一致。
## 2026-10-18 ASSISTANT-DESIGN AI 投研助手改造设计稿与案头核实（仅文档）

### 做了什么

新增 `finscopeAI_deploy/`，放本次助手改造的设计与部署相关文件。当前只有设计稿 `AI助手改造设计.md` 和目录说明，没有改任何代码、配置或现有 `deploy/`。

设计稿覆盖：工具调用式编排器、A 线数据查询工具、B 线动态语料库（来源登记 / 流水线 / 混合检索）、向量化（local / cloud / hashing）、联网搜索、多轮、数据模型变更、分期（5 期）。

### 核实结论（只查官方文档、模型主页与站点条款原文，未写原型、未上服务器）

- 服务器 2 核 / 3.6 GB：`bge-m3`（权重 2.27 GB）放不下；本地向量默认改为 `multilingual-e5-small`（384 维，约 449 MB）。向量列固定 `vector(1024)`，短向量零填充（余弦不变）；pgvector 的 HNSW 上限 2000 维。
- 联网搜索：Google / Bing 网页引擎的条款不允许自动化抓取，DuckDuckGo 条款原文未读到，**SearXNG 这几个引擎不能直接启用**。`web_search` 改为可替换的 `SearchProvider`（`searxng` 引擎白名单默认空，或 `brave_api`），第 4 期前需你决定。
- DeepSeek 官方文档确认支持 OpenAI 格式的工具调用；文档推荐的模型名与运维指南里的 `deepseek-chat` 不一致，需看服务器实际配置并探测。
- 语料授权：美联储内容公有领域（注明来源、第三方材料例外）；国家统计局允许非商业、注明来源的合理转载；SEC 限速 10 次/秒且必须声明 UA。
- 发现：`config.py` 里 `sec_user_agent` 默认是占位邮箱 `research-admin@example.com`，上线前须换成真实联系方式（它是托管配置，以「集成设置」页的生效值为准）。

### 没有验证的

服务器内存余量、线上模型名与工具调用、DuckDuckGo 条款原文、证监会 / 政府网站 / IMF / BIS / ECB 的条款与 robots 路径、`multilingual-e5-small` 的中文检索效果。这些在设计稿「待确认」里列了。

### 验证

纯文档变更，无代码测试；已核对设计稿里引用的代码位置与配置项（`config.py` 的 `sec_user_agent` 默认值）。

## 2026-10-18 ASSISTANT-SPEC AI 投研助手设计稿审阅与技术规格（仅文档）

### 做了什么

- 对照现有代码审阅 `finscopeAI_deploy/AI助手改造设计.md`，问题列成 19 条（R1–R19），写进新文件 `finscopeAI_deploy/AI助手技术规格.md` 第 0 节，并回改设计稿里的错误说法。
- 规格覆盖：后端模块划分、回合两段式生命周期、SSE 协议（兼容旧前端）、模型工具调用客户端与能力探测、编排器循环与预算、A 线工具参数 / 时点 / 行数上限、`calc`、grounding 算法、渲染块结构、两个迁移的 DDL、语料抓取（含 SSRF 防护）、检索、向量化客户端与 `embedder` 服务、搜索接口、配置项、限流、前端、每期验收、部署顺序。

### 采纳的优化（写进规格）

- 流式不被代理缓冲、长等待不断开：响应头 `X-Accel-Buffering: no`、15 秒心跳、nginx 助手接口单独一段（读超时 180 秒）。现有 `frontend/nginx.conf` 是默认缓冲 + 60 秒超时（R20）。
- 降延迟：常见问题直取数据、按句校验按句推送、校验失败改写一次、提示词与工具定义前缀固定、工具结果 5 分钟缓存。
- 第 3 期拆成 3a（纯全文检索）/ 3b（评测不达标才上向量）；向量改为独立表 + `halfvec` + 每模型部分表达式索引，不再补零到 1024 维。补上跨语言问题（中文问英文语料）的处理（R21）。
- 第 1 期拆成 1a（只换底座：两段式回合、新协议、nginx、限流、迁移，回答不变）/ 1b（编排器与工具）。

### 审阅中发现的现有代码问题（未修，只记录）

- 回合在数据库事务里同步调用模型（`ChatRepository.accept`）。
- 上传文档的全文索引只收每块前 32 个词（`search_document` 截断），查询要求所有词同时出现；建议单独开修复 PR。
- `CustomAnalysisService` 的分位按全序列算、不接受历史时点，助手不能拿它答历史时点（会前视）。

### 验证

纯文档变更，无代码测试；规格里引用的类名、函数名、配置项、表字段都在代码里逐个核对过。

## 2026-10-19 ASSISTANT-1A 问答回合底座（两段式回合、SSE 协议、限流、nginx、迁移）

分支 `feat/assistant-1a-turn-lifecycle`，未提交。回答内容不变（仍是旧的白名单转述或固定说明），只换底座。

### 做了什么

- 回合改成两段：短事务写入用户消息和 `streaming` 的空回答并提交；后台线程调用模型；`finish_turn` 用 `UPDATE … WHERE completion_status='streaming' … RETURNING` 做比较并设置，已被取消的回合不会被改写。模型调用不再占数据库事务（规格 R1）。
- SSE：每行 `data:` 带 `type`（`delta` / `done` / `error`），保留旧的 `delta`、`done:true` 键并增加 `status`；15 秒 `: ping` 心跳；重放字节一致；失败回合重放为 `error` 再 `done(failed)`；客户端断开不取消回合。
- 限流：每用户并发 1、后台线程满直接拒绝、每分钟滑动窗口、按上海日历日从数据库计数的每日额度；429 `RATE_LIMITED`、409 `TURN_IN_PROGRESS`。名额在发出结束事件之前释放，所以客户端收到 `done` 后立刻重发不会撞上自己。
- 超过 `ASSISTANT_STALE_SECONDS`（300）仍是 `streaming` 的行，读取时按 `failed` 返回，下次写入落成 `failed`。
- 会话标题：默认标题时用第一问前 30 字（去多余空白）。
- `frontend/nginx.conf` 给助手消息接口单独一段：HTTP/1.1、关闭缓冲、读超时 180 秒；响应头 `X-Accel-Buffering: no`。
- 前端：新增 `api/assistantStream.ts`（SSE 行解析、429/409 的提示文字、空回复文字）；`AssistantView.vue` 增加“生成失败”状态，失败回合可重新生成。

### 变更文件

- 后端：`app/core/config.py`、`app/db/models/chat.py`、`app/repositories/chat.py`（重写，去掉旧 `accept`）、`app/services/chat_reply.py`、`app/services/assistant/{sse,limits,orchestrator,turns}.py`（新）、`app/api/v1/assistant.py`、`app/main.py`、`alembic/versions/20261019_0029_assistant_messages.py`（新）。
- 前端：`nginx.conf`、`src/api/assistantStream.ts`（新）、`src/views/AssistantView.vue`。
- 文档与配置：`docs/API.md`、`.env.example`、`finscopeAI_deploy/AI助手技术规格.md`。
- 测试：`tests/unit/test_assistant_{sse,limits,runner}.py`、`tests/integration/test_assistant_turns.py`（14 个）、`tests/unit/test_chat_reply.py` 加 1 个、`frontend/tests/unit/assistantStream.test.ts`（8 个）。

### 迁移

`20261019_0029`（接在 `20261018_0028` 后）：`chat_messages` 加可空列 `blocks / sources / steps / tool_trace`（JSONB）、`as_of`（Date）、`model`（String 80）、`grounding`（String 8）、`finished_at`；加部分索引 `ix_chat_messages_assistant_created`（`created_at WHERE role='assistant'`）。降级会删除这些列和索引。

### 新增配置

`ASSISTANT_PER_MINUTE=6`、`ASSISTANT_DAILY_LIMIT=200`、`ASSISTANT_WORKERS=4`、`ASSISTANT_HEARTBEAT_SECONDS=15`、`ASSISTANT_STALE_SECONDS=300`，启动时校验为正数。

### 与规格的差异

- 默认标题除「新的问答」外还认「研究问答」，因为前端建会话时用的是后者。
- 心跳间隔和过期秒数做成了配置项（规格里是写死值）。
- 1a 不引入 `ASSISTANT_ENABLED` 开关。
- `blocks / sources / steps / grounding / tool_trace / as_of / model` 列已建好，1a 里不写。

### 验证

- nginx：用真实 nginx 对旧配置（默认缓冲、60 秒读超时）做过流式实测，确认了缓冲和超时问题（规格 R20）。具体耗时数字没有留档；`frontend/nginx.conf` 的新段落尚未在部署环境验证，上线时需要用慢响应再看一次。
- 做过一次变异检查，确认新增的集成用例能拦住回归（已还原，未记录具体改动点）。
- 后端（`backend` 目录，`DATABASE_URL` 指向 54329 的 `finscope-ai-db-1`）：`ruff check .`、`ruff format --check .`（374 个文件）、`mypy app`（238 个源文件）均通过。
- 后端全量 `pytest`（共 733 个用例）在**全新临时库**上全部通过。
- 在长期使用的开发库上全量跑，有 2 个宏观数据用例失败（`test_board_data_flow::test_china_observations_import_is_idempotent_and_reports_bad_rows`、`test_simulation_run::test_mock_simulation_persists_rating_without_an_order`）：开发库里的中国宏观观测值是上次测试残留（CPI 最新值 0.9 而不是 0.8），与问答改动无关；换新库后通过。未去修这两个用例的库依赖，建议单开 PR 让它们自建隔离数据。
- 前端：`npm run typecheck`、`vitest run`（49 个文件、294 个用例）通过。

### 没有做的

编排器与工具调用、服务端渲染块、grounding、`calc` 工具、前端过程步骤展示（1b 及以后）。`finscope_ai_cursor_refactor_guide.md` 里的卡片与可视化放到第 2 期，由服务端生成结构化块，不由模型直接写数字。

### 下一步建议

1b：编排器与 A 线工具（数据直取、`calc`、按句 grounding），先用 `ASSISTANT_ENABLED` 开关灰度。

## 2026-10-19 ASSISTANT-1B 编排器、数据直取、calc 工具、按句核对（灰度开关后）

分支 `feat/assistant-1b-orchestrator`（接在 1a 提交 `d20aa7e` 之后），未提交。**`ASSISTANT_ENABLED` 默认关闭；关闭时（或没有可用模型时）走 1a 的旧回答，行为与 1a 完全相同**——1a 的 14 个集成用例原样通过，没有改动。

### 做了什么

- **灰度开关**：`ASSISTANT_ENABLED` 既是环境变量，也是「集成设置」里的受管配置项（`runtime_settings.MANAGED`），保存后最多 5 秒生效，不用重启；开关关闭、或模型未配置，都回落到旧回答。
- **编排器 `agent.py`**：工具调用循环（默认最多 4 步，每步最多并行 3 个调用，整回合 150 秒预算）；每步开始前和每次模型返回后检查回合是否已被取消；模型调用失败时，如果已经取到数据，就只展示数据（证据卡片 + 固定说明），没取到数据才算失败。
- **数据直取（fast path）**：对「美国 CPI」「贵州茅台最近一年」「沪深300 PCR」这类明确的问法，服务端不等模型决定，直接取数；结果作为合成的工具调用和工具结果放在用户问题**之后**注入对话，模型第一步就能直接写答案。对不上或有歧义（如「PCR 现在多少」没说哪个指数、同时问两个指数）就不猜，交给模型。可用 `ASSISTANT_FAST_PATH=false` 关掉。
- **9 个只读工具**（`tools/`）：`instrument_search`、`price_bars`、`financial_metrics`、`board_strength`、`macro_series`、`macro_compare`、`custom_indicator`、`watchlist`、`calc`。所有工具都按**当天可见的数据**取数（美股和美国宏观序列按纽约日历日，A 股和中国宏观按上海日历日，发布当天不可见；`custom_indicator` 只回答“现在”）。结果统一成「证据」，编号 `E1…`，**取不到的结果也登记编号**，模型可以引用并如实说“没有数据”。
- **`calc`**：只接受对证据单元格的引用和运算（`E1.c3 / E2.c3 - 1`），`Decimal` 计算、保留 4 位小数；不接受自由数字和函数调用，被零除、缺失单元格返回明确错误。
- **按句核对（grounding）**：回答按句拆开，每句里的数字、日期都要能在本回合证据（含 `calc` 结果）里找到；`sources` 为空，所以任何 `[n]` 引用都视为无效。遇到第一句过不了核对就停，把已推出的前文和对不上的内容交给模型，让它接着写一次；再不行就只保留已经通过核对的前文，并追加一个 `NOT_GROUNDED` 警告块（有前文时是“后续内容没能通过核对，已隐藏。”，一句都没通过时是“没能生成可核对的文字回答，上面是查到的数据。”），`grounding` 记为 `partial` / `none`（全部通过为 `full`）。核对通过之前不向浏览器推送该句。
- **服务端渲染块**：`line_chart` / `metric_cards` / `table` / `notice` 由服务端根据证据生成（图表和数字不经过模型）；块先写入数据库再推给浏览器，回合中途掉线也不会丢；重放时顺序固定为步骤、块、文字、`done`。
- **不支持工具调用的模型**：首次使用时探测一次，只有接口明确拒绝（400/404/422）或模型不调用探测工具才缓存为“不支持”，此时改走规划器模式（模型输出 JSON 计划，服务端执行，再让模型叙述）；限流、超时、鉴权失败不缓存，下一回合重新探测。
- **工具结果缓存**：LRU 256 项、5 分钟；不缓存 `watchlist`、`calc` 和“不可用”结果。
- **连续追问**：把最近 3 个已完成回合（最多 6 条消息，回答截断到 1200 字，空回答回合跳过）作为历史；另从已存的 `tool_trace` 里取最多 8 个实体（只取代码、名称、市场，不取内部 id）作为“上文提到的证券”，让“它呢”这类追问能接上。
- **落库**：回合结束的比较并设置里一并写入 `blocks / steps / as_of / model / grounding / tool_trace`（`tool_trace` 为对象，不存结果行；字段见规格 §1 末尾）；接口 `MessageItem` 增加这些可选字段。
- **资料文本消毒**：资料里的文字单元格（证券名、分组名等）会把换行、`<`、`>`、`|`、双引号替换成无害字符并限制 120 字，防止伪造 `</资料>` 跳出数据块（该问题在自查时发现并修复，并用变异检查确认测试能拦住）。
- **提示词** `app/prompts/assistant_v1.txt`（`PROMPT_VERSION=assistant_v1`）：只能用资料里的数据；不写 `[n]`；不给买卖建议；工具定义按名字排序、前缀稳定，便于模型侧缓存。

### 变更文件

- 新增（`backend/app/`）：`services/assistant/{agent,responder,results,evidence,calc,sentences,grounding,blocks,tool_cache,fast_path,llm_tools}.py`、`services/assistant/tools/{__init__,base,macro,market,analysis}.py`、`prompts/assistant_v1.txt`。
- 修改：`api/v1/assistant.py`（用 `AssistantResponder`，响应字段）、`core/config.py`（4 个配置 + 校验）、`db/models/chat.py`（`tool_trace` 类型标注，无迁移）、`main.py`、`repositories/chat.py`（`append_block`、`turn_status`、`recent_turns`、`finish_turn` 带 extras）、`services/assistant/{orchestrator,sse,turns}.py`、`services/chat_reply.py`、`services/runtime_settings.py`。
- 文档与配置：`docs/API.md`（“数据工具”小节，并更正 1a 写错的并发第二问错误码）、`.env.example`、`finscopeAI_deploy/AI助手技术规格.md`（状态行与 1b 差异）。
- 测试：`tests/assistant_fakes.py`（共用的假模型/假数据）；新增单元 `test_assistant_{calc,grounding,sentences,evidence_blocks,fast_path,tools_macro,llm_tools,agent,config}.py`；新增集成 `test_assistant_tools.py`（10）、`test_assistant_agent_turns.py`（14）；`test_assistant_{runner,sse}.py` 补用例。

**没有数据库迁移**：1a 已经建好所有列。

### 新增配置

`ASSISTANT_ENABLED=false`、`ASSISTANT_MAX_STEPS=4`（1–8）、`ASSISTANT_FAST_PATH=true`、`ASSISTANT_TURN_SECONDS=150`（>0）。启动时额外校验 `ASSISTANT_STALE_SECONDS ≥ ASSISTANT_TURN_SECONDS + 30`，否则正常运行的长回合会被读成“已失败”。

### 与规格的差异

见 `finscopeAI_deploy/AI助手技术规格.md` §1 末尾“1b 实现与本规格的差异”，要点：`tool_trace` 是对象不是数组；接口缺省而不是 `null`；1b 没有来源，不发 `sources` 事件、不写 `[n]`；能力探测在首次使用时做且只缓存明确拒绝；规划器模式用 JSON 计划；资料文本消毒；取消检查的位置。

### 验证

- `backend` 目录，`DATABASE_URL` 指向 54329 的 `finscope-ai-db-1` 上的**全新临时库**（`alembic upgrade head` 后）：全量 `pytest` 通过 **929 个用例，1 个跳过**（1a 时为 733）。跳过的是 `tests/integration/test_simulation_run.py:126`，库里没有 600519.SH 日线时跳过，与助手无关。
- `ruff check app tests`、`ruff format --check app tests`（372 个文件）、`mypy app`（254 个源文件）均通过。
- **变异检查**（改坏后确认测试会失败，已还原并扫描无残留）：① 去掉 `evidence` 的文本消毒，3 个用例失败；② 关掉 `_push` 里的按句核对，7 个用例失败（6 个单元、1 个端到端：编造数字不能到达浏览器）。
- 长期使用的开发库上跑 `test_simulation_run.py`：`test_mock_simulation_persists_rating_without_an_order` 失败，是 1a 日志已记录的“开发库里中国宏观观测值是上次测试残留”问题，与本次无关；依赖 600519.SH 日线的 `rules_engine` 用例在开发库上没有被跳过，也没有失败输出。
- 前端没有改动，所以没有重跑前端检查。

### 没有做的（以及已知的限度）

- **没有用真实模型跑过**。所有涉及模型的测试用的是脚本化的假模型（工具调用循环、规划器模式、重写、失败、取消、追问都覆盖了），验收里“配置模型后能回答三个问题”目前只验证了：这三类问法会被数据直取识别（单元测试），工具对测试库里的数据能取到并渲染（集成测试），整条回合的流式、落库、重放（假模型）。真实模型对提示词的遵守程度、第一问的耗时要灰度时看。
- 前端**还没有**渲染 `steps` / `blocks`（接口已经返回，流里也有）；用户在前端看到的仍是文字回答。步骤展示和卡片/图表渲染是第 2 期。
- 没有来源和 `[n]` 引用（3a 的语料检索之后做）。
- 没有在部署环境验证 nginx 新配置（1a 遗留）。
- `ASSISTANT_ENABLED` 的灰度只有全局开关，没有按用户放量。

### 下一步建议

1. 在测试环境配好模型、打开 `ASSISTANT_ENABLED`，用“美国 CPI 最近怎么走”“贵州茅台最近一年涨跌”“沪深 300 PCR 现在多少”各问几次，看 `tool_trace`（`mode`、`calls`、`failure`、`repaired`）和耗时，再决定要不要调 `ASSISTANT_MAX_STEPS` 或提示词。
2. 第 2 期：前端渲染步骤和块（块已经由服务端生成）。
3. 3a：语料检索与 `[n]` 引用。

## 2026-10-20 ASSISTANT-2 前端渲染步骤与数据块；核对放行统计窗口；重写不再重复前文

分支 `feat/assistant-2-render-blocks`（接在 PR #30、#33 合并后的 `master` 上），未提交。起因是 1b 上线后试「沪深 300 PCR 现在多少」，页面只剩一句「分两个合约组：」且出现两遍，没有数字。

### 原因（三处叠在一起）

1. **前端不渲染**：1b 的服务端早已生成表格、折线图、指标卡和核对提示，也写进了消息，但 `AssistantView` 只读 `delta` 和 `done`，用户看不到任何数据块，也看不到「后续内容已隐藏」的提示。
2. **核对误拦**：期权 PCR 工具的列名是「成交量 PCR 5 年分位」。核对只放行紧贴汉字或字母的序列名数字，中间一个空格就对不上；模型只要在回答里提到「5 年分位」（写 `5 年` 或 `5年` 都一样），这一句就被拦，后面全部隐藏。用与真实工具同样的列名复现并确认。
3. **重写重复**：核对失败后让模型「接着往下写」，它把已展示的第一句又写了一遍，旧代码照单推送，所以看到引言出现两次。

没有在服务器上复现过真实的模型回答（本机没有模型），原因 2 是用工具的真实列名和模型最常见的几种写法复现的；服务器上那次回答具体被拦的是哪一句，以日志里新增的一行为准（见下）。

### 做了什么

- **前端**：新增 `src/api/assistantBlocks.ts`（步骤和块的收窄解析、流式事件并入、折线图配置，字段不对的块直接丢弃）、`components/assistant/ReplySteps.vue`（查了哪些数据：查的时候展开，查完收成一行摘要）、`components/assistant/ReplyBlocks.vue`（指标卡、折线图、表格、提示；表格最高 260px 可滚动，空值显示 `—`，文字当文字显示不当 HTML）。`AssistantView.vue` 在每条回答里按「步骤 → 正文 → 核对提示 → 数据块 → 资料截止日」排；流式时逐个事件并入，刷新后从历史消息的 `steps / blocks / as_of / grounding` 读出同样的内容；旧消息和旧协议（只有 `delta` / `done`）不受影响。
- **核对**：序列名里的数字按去掉空格后的名字比较；列名里写明的统计窗口（`N 年分位`、`N 个月分位`）在「这句话说的是分位」时放行 `N 年`。没有放开的：光秃秃的数字、别的窗口长度、别的单位、`N 年均值`、资料里只有 `10年期` 时的 `10年`、资料里没有的合约代码。
- **重写**：改写得到的句子里，与已展示句子完全相同的不再推送。
- **提示文字**：核对没过时的两条提示不再说「上面」（数据块现在在正文下方），改为「请看下方查到的数据」。
- **提示词 v2**（`prompts/assistant_v2.txt`，`PROMPT_VERSION=assistant_v2`，旧文件改名）：告诉模型表、图、指标卡在回答下方，用「下表」「下图」指代；每个要点写成带关键数字的完整句子，不要只写引子。
- **排错日志**：回答有句子没通过核对时，后端记一行 warning，只含没对上的数字和日期（模型写的数，不含整句、密钥或用户资料）。

### 变更文件

- 前端：`src/api/assistantBlocks.ts`、`src/components/assistant/{ReplySteps,ReplyBlocks}.vue`（新）、`src/views/AssistantView.vue`。
- 后端：`app/services/assistant/{agent,grounding,responder}.py`、`app/prompts/assistant_v2.txt`（由 v1 改名）。
- 测试：前端新增 `assistantBlocks.test.ts`（18）、`ReplyBlocks.test.ts`（6）；后端 `test_assistant_grounding.py` 加 8、`test_assistant_agent.py` 加 3（两个重复前文、一个提示词版本与内容），两处断言里的提示词版本改为 v2。
- 文档：`finscopeAI_deploy/AI助手技术规格.md`（提示词文件名与版本）、本日志。

没有接口、数据库或配置变化。

### 验证

- 后端（`backend` 目录，全新临时库，`alembic upgrade head` 后）：`pytest` 单元 821 通过，集成 143 通过、1 个跳过（`test_simulation_run.py:126`，库里没有 600519.SH 日线，与本次无关）；`ruff check`、`ruff format --check`（375 个文件）、`mypy app`（254 个源文件）通过。
- 前端：`vitest run` 53 个文件、331 个用例通过；`npm run typecheck`、`npm run build` 通过。第一次整体跑时 `navigation.test.ts` 有 1 个用例超时（与后端测试同时在跑），单独重跑和随后整体重跑都通过。
- 变异检查（改坏后确认测试会失败，已还原并扫描无残留）：关掉重写去重，2 个用例失败；去掉「这句话说的是分位」的限制，1 个用例失败（`5 年均值` 被放行）。
- 组件测试用占位替换了 ECharts 画布，只验证传给图表的配置和表格、卡片、提示的文字；**没有在真实浏览器里看过页面排版**，也没有用真实模型再跑一次那三个问题。

### 没有做的

- 核对仍然很严：`PCR 低于 1` 这类资料里没有的比较数字、模型凭记忆写的合约代码，依旧会被拦。这是设计，不放宽；真实回答里被拦得多不多，要看服务器日志里新增的那行，再决定是不是调提示词。
- 来源和 `[n]` 引用（3a）。
- 图表目前只画 `line_chart` 一种；没有为期权 PCR 单独做图（它是表格）。

### 下一步建议

部署后用同样的三个问题再试，对照页面上的「查了 N 项数据」摘要、数据块和 `资料截止` 行；如果还有句子被拦，看后端日志里 `助手回答有句子没通过核对` 那一行的数字，再决定放行规则或提示词怎么改。

## 2026-10-20 ASSISTANT-2B 回答正文按 Markdown 子集渲染；来源角标；只剩数据块的回答不再消失

同一分支，接在上一条之后。规格 §1 第 2 行、§17 的前端部分到此做完；按「整个 AI 板块做完再一起提 PR」，这条没有单独提。

### 做了什么

- **Markdown 子集**（`src/api/assistantMarkdown.ts` 自己写的解析器，没有新增依赖）：小标题（`一、`、`#`）、段落、无序 / 有序列表、表格（最多 200 行、12 列，行短补空、行长截断）、行内加粗、行内代码、`[n]` / `【n】` 引用。解析结果只有文本节点，模板里没有 `v-html`；原始 HTML 一律当文字显示。没写完的格式（流式中途的 `**3.1`、只有一行的表头）按原文显示，不会闪烁成别的东西。`3.1% 是…`、`-5.2% 是…`、`2026-10-16 的…` 不会被当成列表。
- **来源角标与清单**（`MarkdownText.vue`、`MarkdownInline.vue`、`ReplySources.vue`）：`[n]` 只有在来源清单里登记过才画成角标，否则原文显示，不替模型编来源；链接只放行 `http` / `https`（`javascript:`、`data:`、`//` 都不放行，退化成不可点的角标）；网页来源恒标「未经核验」，哪怕对方字段写成已核验也不信。后端目前还没有发 `sources` 事件（3a 才有），所以这部分只在组件和单元测试里验证过，浏览器里看不到。
- **只剩数据块的回答会消失（实测中发现并修复）**：每句话都没通过核对时，回答正文落库为空串、只有数据块和「没能生成可核对的文字回答」提示。`readTurn` 用 `asString`，对空串返回 `null`，随后 `content === null` 就把整条消息丢了，页面上只剩用户自己的提问。这个判断在 `master` 上就有（模板里本来为失败、取消、生成中、完成但没内容各写了一句提示，说明设计上是要显示的），以前已完成的回答都有正文所以没人碰到，1b 之后才暴露。现在解析抽成 `assistantStream.ts` 的 `parseTurn`：正文允许是空串，缺 id、缺角色或正文不是字符串才丢弃。附带效果：失败、取消、仍停在生成中的空回答刷新后也会显示成对应的状态，不再凭空消失。

### 变更文件

- 前端：`src/api/{assistantMarkdown,assistantBlocks,assistantStream}.ts`、`src/components/assistant/{MarkdownText,MarkdownInline,ReplySources}.vue`（新）、`src/views/AssistantView.vue`（去掉旧的逐行渲染和相关辅助函数）。
- 测试：新增 `assistantMarkdown.test.ts`（13：解析、渲染、XSS、来源、`safeUrl`）；`assistantBlocks.test.ts` 加 1（`sources` 事件）；`assistantStream.test.ts` 加 5（`parseTurn`）。
- 没有后端、接口、数据库或配置变化。

### 验证

- 前端：`vitest run` 54 个文件、350 个用例通过；`npm run typecheck`、`npm run build` 通过。
- 变异检查：把 `parseTurn` 改回 `asString`，2 个新用例失败；已还原并扫描无残留。
- **浏览器实测**（临时库 + 本地后端 + vite 开发服务器，登录后在页面里操作；已清理）：
  - 先在开关关闭时问一次，再打开开关重启后端，同一会话里旧消息照常显示，没有多出空块、步骤或空白；
  - 新回答：步骤收成「查了 1 项数据」、加粗和列表、折线图、指标卡、「资料截止」都显示；刷新后同样恢复；
  - 全部句子没通过核对的回答：显示提示 + 折线图 + 指标卡（修复前这条整条消失）；
  - 模型回复里带 `<img onerror>`、`<script>`、`<b onclick>`：页面上是原样文字，DOM 里没有 `img` / `script` / 事件属性，拦截 `alert` 没有被触发。
- **这次实测用的是剧本假模型和假数据工具**（本机没有真实模型），后端、数据库、SSE 流和前端都是真的。没有验证：真实模型的措辞在页面上好不好看；表格块（没有哪个假工具产出表格，只在组件测试里验证）；窄屏排版；来源角标（后端还没发）。
- 实测中另外看到的两点，没有改：假回复里写了「下表列出了…」，而这次数据块只有折线图和指标卡，页面上没有表——提示词 v2 让模型在有表时才说「下表」，是否真能做到要看真实模型；一次被拦的回答会显示数据块和「没能生成可核对的文字回答」，这是预期行为。

### 没有做的

- 来源 / `[n]` 引用与语料检索（3a）；网页检索（3b）。
- 窄屏下的排版、真实模型回答的措辞和排版，需要部署后在真实环境里看。

## 2026-10-20 ASSISTANT-3A0 情景假设：`calc` 可以带假设数字，假设只能写在标明假设的句子里；提示词 v3

分支 `feat/assistant-3a0-assumptions`。知识库设计见 `finscopeAI_deploy/AI助手知识库设计.md`（新），规格 §1 的分期按它改成 3a-0 / 3a-1 / 3a-2 / 3a-3 / 3c / 4 / 3b / 5，这条是 3a-0。

### 为什么

参考问答里大量是「若估值从 30 倍压到 20 倍会怎样」「降息 25 个基点对应什么」这类情景推演。原来的核对只认资料里的数字，模型给的假设数字一律被拦，答不了；如果直接放开，假设数字又可能被写成事实。

### 做了什么

- **`calc` 的假设输入**（`calc.py`）：每个输入要么引用资料（`evidence` + `column` [+ `row`]），要么是假设（`assume` + `label`），由校验器保证二选一；两种写法放在同一个对象里，不用联合类型，生成给模型的 schema 里没有 `anyOf`。假设值限制在 ±1e12 以内。用到假设的结果标为 `ToolResult.hypothetical`，并带上 `assumptions`（假设名, 值）；连环计算会把上游的假设一起带上（去重）。标题加「（情景假设）」，来源只写假设名，不写数字。
- **核对规则**（`grounding.py`、`evidence.py`）：假设证据的数字（结果和假设值）单独放在一个池里，只有句子里出现「假设 / 假定 / 假如 / 如果 / 倘若 / 若 / 情景」之一时才放行；「若干」不算。假设证据不进事实数字、日期和序列名的放行范围，所以模型给假设起个带数字的名字（如「当前市盈率30倍」），也不能借「序列名」放行「当前市盈率 30 倍」；只用假设算出的结果截止日是今天，今天也不会因此被放行。
- **给模型的资料文本**：假设证据多一行「（情景假设，不是事实；只能写在带「假设 / 若 / 如果」的句子里。假设：…）」，假设名照样做了防注入处理。
- **页面上的标注**（`blocks.py`）：每次假设计算出一条 `info` 提示（`code = ASSUMPTION`）：「情景假设（不是查到的数据）：假设回落幅度 = 10；算得 313」。前端已经支持 `info` 级提示，归在数据块那一组，不需要改前端。
- **提示词 v3**（`prompts/assistant_v3.txt`，由 v2 改名，`PROMPT_VERSION=assistant_v3`）：回答形状改为「结论 → 依据 → 情景（只在问到影响或推演时）→ 还需要核实（不写数字、不猜内容）」；说明假设数字用 `calc` 的 `assume` 给出、只能写在带「假设 / 若 / 如果」的句子里；「不预测」改为「情景推演只说明在这个假设下会算出什么，不说哪个情景会发生」。

### 变更文件

- 后端：`app/services/assistant/{calc,evidence,grounding,blocks,results,agent,responder}.py`、`app/services/assistant/tools/__init__.py`（`calc` 工具说明）、`app/prompts/assistant_v3.txt`。
- 测试：`test_assistant_calc.py` +7（假设结果、与资料混用、连环传递、二选一校验 7 种非法写法、越界、schema 扁平）；`test_assistant_grounding.py` +4 组（标明假设的 5 种写法放行、当事实写的 4 种被拦、日期不被假设放宽、标了假设也不放行无关数字）；`test_assistant_evidence_blocks.py` +2；`test_assistant_agent.py` +2（整条回合：标明假设的回答完整通过并出提示块；把假设结果当事实写会被拦下改写）；提示词版本号断言改为 v3。
- 文档：规格 §1 分期表与状态行、§6 提示词版本、§7 `calc`、§8 第 5 条；知识库设计 §7、§11（`quote_table` 挪到 3a-1：逐字核对要用检索片段）。
- 没有接口、数据库、配置或前端变化。

### 验证

- 后端：`ruff check`、`ruff format --check`、`mypy app` 通过；`pytest tests/unit` 全部通过；助手的 38 个集成测试在一次性 `pgvector/pgvector:pg16` 测试库（端口 55432，跑完已停掉）上全部通过。
- 变异检查：让假设池不看句子里有没有「若」，4 个新用例失败；让假设证据的标题 / 来源进序列名，「当前市盈率 30 倍」那条失败；均已还原并确认无残留。
- 写端到端用例时发现，假数据里本来有 320.1，按「≥100 允许取整」的现有规则「320」对得上事实，所以用例改用不和任何事实重合的 313。这说明假设结果恰好接近某个事实数字时，句子不写「若」也能过——它确实等于资料里的数，这是现有取整规则的结果，不是漏洞。
- 全量单测里 `test_filing_report.py::test_rules_report_uses_only_stored_sample_numbers` 偶发失败一次，重跑通过：报告正文里还有别的随机内容偶尔带出「999」，触发 `"999" not in body`。这是原有的不稳定用例，与这次改动无关，没有在这个分支里修。

### 没有做的

- 没有用真实模型试过：模型会不会主动用 `assume`、会不会在情景句里写「若」，要部署后看。
- `quote_table`、来源与 `[n]`、按引用核对放在 3a-1。

## 2026-10-20 ASSISTANT-3A1 语料底座：用户文档进语料库、`corpus_search`、来源编号与按引用核对、`quote_table`、检索评测集

同一分支 `feat/assistant-3a0-assumptions`，按知识库设计 §11 的 3a-1 分五步提交。总开关 `CORPUS_ENABLED`（默认关），关着时助手行为与 3a-0 完全相同。

### 做了什么

- **语料表**（迁移 `20261020_0030_corpus`）：`corpus_sources / corpus_documents / corpus_chunks / corpus_fetch_runs`。文档带 `kind`（filing / official / reference / user）、版本与内容哈希（同 URL 内容变了记新版本，检索只看最新版本）、`owner_user_id`（约束：`kind = user` 当且仅当有上传者）；用户文档副本通过 `user_document_id` 挂在「我的文档」上，原文件删除时级联删除。预置一个 `user_upload` 来源。
- **全文检索**（`repositories/corpus.py`、`services/corpus_text.py`）：中文写成二字词串进 `simple` 配置，**全部保留**（旧的 `document_chunks` 索引只留前 32 个词，长块后半段查不到）；英文用 `english` 配置（词形还原）。权重：标题 A、块头 B、正文 D。每个关键词一个 `plainto_tsquery`（词内同时出现），关键词之间任一命中。先过滤（最新版本、本人可见、`published_at <= as_of` 不前视、类别 / 代码 / 报告类型 / 期次 / 发布方 / 时间窗），再按 `ts_rank_cd × 半衰期衰减`（公告与官方文本 30 天，其余不衰减）排序，同一文档最多 2 块，取前 8，低于 `CORPUS_MIN_RANK` 的不给。
- **块头**：模板拼「发布方｜代码｜标题｜报告类型｜期次｜章节」，不调模型。**不放发布日**：评测里「10月份 CPI」因为块头里的 `2026-10-15` 命中了 9 月数据；时间范围交给 `since` / `until`。报告类型写中文（「业绩预告」），所以标题叫「业绩预亏公告」的也能被「业绩预告」查到。
- **用户文档接入**（`services/corpus_ingest.py`、`document_index.py`）：「我的文档」解析成功后按语料库自己的分块参数（目标 280 / 上限 360 / 重叠 40 词）另写一份副本，只对上传者可见；写副本失败只记日志，不影响原有的文档检索。已有上传用 `python -m app.scripts.corpus_user_backfill` 补，可重复运行。
- **`corpus_search` 工具**（`tools/corpus.py`）：中英文关键词各最多 8 个（英文来源必须给英文关键词，中文关键词查不到英文文档）、类别 / 代码 / 报告类型 / 期次 / 发布方 / 时间窗过滤。结果的截止日取最新一篇的发布日，不用今天。含私有文档，不进工具缓存；不出数据块，只在过程里列一步「检索资料」。
- **来源编号**（`evidence.py`）：检索到的片段按出现顺序编成 [1]、[2]…，全回合统一，同一片段重复检索不换号。给模型的资料是「[n] 标题｜发布方｜日期｜第 x 页｜章节」加「原文：…」（单段最多 2400 字，照样做防注入处理）。
- **`sources` 事件与保存**（`agent.py`、`sse.py`、`repositories/chat.py`）：一句话通过核对、且引用了新的 [n]，先推 `sources`（整表）再推这句的 `delta`，角标出现时来源已经在了。来源存进 `chat_messages.sources`，消息接口带回，同一个 `client_message_id` 重发时按「步骤 → 块 → 来源 → 正文」重放。前端第 2 期已经支持这个事件，不用改。
- **按引用核对**（`grounding.py` 第 7、8 条）：片段里的数字和日期只对写了它的 `[n]` 的句子放行，数字是真的、出处挂错照样拦；`[n]` 不存在也拦。模型写的东西（情景假设的名字、摘录表的标题和文字）不进日期 / 序列名 / 标识符的放行范围。
- **`quote_table`**（`tools/quote.py`）：模型给每行「来源 n、原文、倾向、解读」，服务端核对原文确是来源 [n] 片段的原话（NFKC 归一、按资料文本里的显示方式转换、去掉空白后比较子串），解读按「引用了 [n] 的句子」核对数字和日期，标题也核对；一行不过关整张表不出，原因只回给模型（不给读者出提示），模型可以改了再调。通过的表作为普通 `table` 块推出（列：来源、原文、倾向、解读）。
- **日期写法**：数字和「年 / 月 / 日」之间带空格（「9 月」「2026 年 9 月 1 日」）也按日期核对。原来只认「2026 年」，「9 月」会被当成裸数字 9 拦下。
- **评测集**（`tests/eval/corpus_eval.json`、`services/corpus_eval.py`、`tests/integration/test_corpus_eval.py`）：18 篇合成文档（仿写的美联储声明 / 纪要 / 讲话 / 预测摘要 / 褐皮书，仿写的统计局 CPI / PPI / GDP，虚构公司 T0000x 的业绩预告 / 快报 / 问询函回复，两篇参考资料，一份用户 a 的私有纪要）；54 题：42 题能答（其中 21 题是中文问英文语料），12 题应当拒答（库里没有、被过滤条件排除、不前视、别的用户查私有文档）。题目的关键词固定写好，评测只量检索，进 CI。指标：Recall@5、拒答率、私有文档泄露（必须为 0）。
- **真实模型冒烟**（`app/scripts/assistant_corpus_smoke.py`，不进 CI）：同一套语料和题目，交给配置的模型自己出关键词、写回答，统计调了检索的比例、引用命中期望文档的比例、引用了期望以外文档的比例、核对全部通过的比例、该拒答时没引用资料的比例、摘录表通过率。会往库里写评测数据（结束时删掉），必须加 `--test-db` 才跑。

### 与规格 / 设计的差异

- 没有默认 90 天的时间窗：时效靠半衰期衰减，模型要限时间就给 `since` / `until`。默认窗会让「半年前的问询函」这类问题查不到。
- 没有可信度系数（设计 §6 的「× 可信度」）：目前只有用户文档一类，等公告和官方文本进来、评测显示需要时再加。
- `PoliteClient` 挪到 3a-2：只有抓取用得到。
- `quote_table` 没有做成规格 §9 里单独的 `quote_table` 块类型，而是推普通的 `table` 块，前端不用改；原文与译文并排、原文可展开的样式以后要再做。
- 逐字比较是「去掉空白」而不是「合并空白」：PDF 抽出来的中文常在字之间夹空格或换行。
- 块头不放发布日（见上）。

### 变更文件

- 迁移：`alembic/versions/20261020_0030_corpus.py`。
- 后端：`repositories/corpus.py`（新）、`repositories/chat.py`；`services/corpus_text.py`、`corpus_ingest.py`、`corpus_eval.py`（新）、`document_index.py`、`chat_reply.py`；`services/assistant/{agent,evidence,grounding,blocks,results,sse,turns,responder,tool_cache}.py`、`tools/{__init__,corpus,quote}.py`（后两个新）；`prompts/assistant_v3.txt`（检索与 `quote_table` 的用法、`[n]` 引用规则）；`core/config.py`、`main.py`；脚本 `scripts/corpus_user_backfill.py`、`scripts/assistant_corpus_smoke.py`（新）。
- 测试：`test_corpus_repository.py`、`test_corpus_eval.py`、`test_corpus_text.py`、`test_assistant_tools_corpus.py`（新）；`test_document_search.py`、`test_assistant_agent_turns.py`、`test_assistant_{agent,grounding,evidence_blocks,sse}.py` 增补；`assistant_fakes.py` 加假的检索工具。
- 配置与文档：`.env.example`（`CORPUS_ENABLED`、`CORPUS_MIN_RANK`）、`docs/API.md`（`sources` 事件、`quote_table`）、规格状态行、知识库设计 §11。
- 前端没有变化。

### 验证

- 后端：`ruff check`、`ruff format --check`、`mypy app` 通过；`pytest tests/unit` 全部通过；语料、文档检索、助手、对话相关的集成测试在一次性 `pgvector/pgvector:pg16` 测试库（端口 55432）上全部通过，迁移降级再升级通过。
- 检索评测：改块头之前 Recall@5 = 0.988、拒答率 = 0.917（漏的两题就是上面块头的两个问题）；改后两项都是 1.000，泄露 0。评测集是自己写的、关键词也是自己给的，满分只说明检索链路没有退步，不说明真实问题上的效果；CI 下限设为 0.9。
- 变异检查：检索的可见性条件改成 `OR true`，私有文档用例失败；`_identifiers` 改回扫描全部证据，摘录表用例失败（见下）；均已还原。
- 写 `quote_table` 用例时发现一个漏洞并修掉：标识符放行（证据表文字列里出现过的数字串，本来是给证券代码用的）扫的是**全部**证据，所以摘录表的解读里写了「2.9」，叙述里不引用 [1] 也能写 2.9。改为只扫事实证据（假设证据、摘录表除外），情景假设的文字列也一并排除。

### 上线步骤

1. 迁移到 `20261020_0030`；
2. 跑一次 `python -m app.scripts.corpus_user_backfill`（容器里是 `--root /app/data/uploads`）；
3. 设 `CORPUS_ENABLED=true` 并重启（这个开关不在运行时设置里）。

### 没有做的

- 真实模型冒烟没有跑：本机没有配模型。上线前用测试库跑一次 `assistant_corpus_smoke.py --test-db`，看模型给的关键词能不能查到、引用挂没挂对、会不会主动用 `quote_table`，结果补在这里。
- 前端来源清单不显示页码（接口已经给了 `page_start` / `page_end`）。
- 公告、美联储、统计局的抓取在 3a-2 / 3a-3；那之前语料库里只有用户自己的文档。

## 2026-10-20 ASSISTANT-3A2 公告正文：巨潮现抓与定时抓，美股 8-K

分支 `feat/assistant-3a2-filings`。`CORPUS_ENABLED` 打开后，模型多一个 `filing_fetch`。

### 做了什么

- **`PoliteClient`**：每主机 1 次/秒，响应最多 5 MB，重定向最多 3 次且每一跳都要在来源白名单里。解析到私网、回环、链路本地地址就拒绝。403 / 429 立刻停止，不重试。`SEC_USER_AGENT` 里没有真实邮箱（空的，或仍是 example.com）时不发出抓取。
- **A 股**：`filing_fetch` 先查巨潮目录，选中最匹配的一份，下载 `static.cninfo.com.cn` 上的 PDF，解析后写入语料库，同一回合就能引用。同一地址已经入库的不再下载。本回合超过 20 秒就把任务交给 worker，回答里留下标题和链接。
- **定时**：财报季（1、4、7、8、10 月）工作日 9:00–21:00 每 30 分钟扫全市场业绩预告和快报；其余月份每个工作日 18:00 扫一次。全部用户的自选股每个工作日 18:05 和 21:05 扫定期报告、预告、快报和问询函。每次最多下载 15 份。开关 `SCHEDULER_ENABLE_CNINFO_FILINGS`，默认开；没有真实联系邮箱时只记日志，不登记任务。
- **美股 8-K**：问到时现抓 submissions，正文优先 Exhibit 99.1，没有就用主文件。HTML 去掉 script / style。超时同样转后台。
- 迁移 `20261020_0031` 登记巨潮和 SEC 两个来源。

### 没有做的

- 10-K / 10-Q 的 MD&A。
- 自选美股的 8-K 定时抓，目前美股只在问到时抓。
- 本机没有对巨潮和 SEC 做真实下载。上线后要用真实邮箱，并拿一份已入库的 A 股业绩预告试 `filing_fetch`。

## 2026-10-21 ASSISTANT-3A3 官方文本与联网检索：访谈、讲话、新闻也能问

分支 `feat/assistant-3a3-ask`。公告库答不了「某人前几天的访谈偏鹰还是偏鸽」：官方 RSS 只有有讲稿的讲话，访谈通常没有文字稿。

### 做了什么

- **官方文本**（迁移 `20261021_0032`）：每小时抓美联储讲话、新闻稿（含 FOMC 声明和纪要）、证词 RSS，以及国家统计局「数据发布」栏目第一页。正文写入语料库，可以长期引用。调度器每次启动时，还没有任何正文的来源会自己补最近 20 篇，不用手跑命令。RSS 和栏目页只有最近一批，不是全历史。需要再补时用 `python -m app.scripts.corpus_official_backfill`。
- **`web_search`**：语料库没有原文时，中文优先博查、英文优先 Brave。摘要标「未核实」，句末要写 `[n]`。搜索结果不进语料库，也不写入回答记录；刷新后提示可以重新提问。若结果落在美联储或统计局官网，改由本系统抓原文入库。
- 开关在「集成设置」：启用联网检索、Brave 密钥、博查密钥。默认关。另有每天每用户次数上限 `ASSISTANT_WEB_DAILY_LIMIT`（默认 30）。明显不能检索的词（例如自制炸弹）直接拒绝，宏观和政策问答不拦。

### 没有做的

- 没有 Brave / 博查密钥就仍然答不了没有文字稿的访谈。密钥要用户自己申请。
- 博查结果在书面确认前也不保存。
- 证监会、交易所、IMF / BIS / ECB 仍是第二批。人民银行按 robots 不抓。

## 2026-10-21 ASSISTANT-3A4 联网检索可靠性：服务出错不再说成「查不到」

分支 `feat/assistant-web-reliability`。起因：用户问「马斯克前几天在推特上说了什么」，助手回答「查不到」，一周、一个月、中文、英文共查了 4 次，全是空。

### 查到的原因（2026-10-21，在服务器后端容器里只读调用，没改任何东西）

- 博查返回 **HTTP 403** `You do not have enough money or package quota`，即余额不足；Brave 没配密钥，而且从这台服务器直连 `api.search.brave.com` 15 秒超时，配了密钥也未必能用。
- 代码只认 HTTP 200，403、401、429、超时都按「空结果」处理，工具只告诉模型「网上没有查到可用的结果」，模型就答「查不到」。
- 用户给博查充值后，同样的问题已能查到（2026-10-18 起多家媒体的报道：马斯克称美债问题只能靠 AI 和机器人解决）。限定最近一周比不限时间更准；不限时间时前几条是 2025 年的旧闻。

### 做了什么

- **服务出错和没有结果分开**：新增 `SearchServiceError`。所有配置过的搜索服务都出错（密钥无效或额度不足、请求过多、服务故障、网络超时），或一个密钥都没有时，`web_search` 返回 `unavailable`，原因写明「联网检索服务暂时不可用（博查：密钥无效或额度不足），不是网上没有这条消息」，并要求模型如实告诉用户、不要说查不到、不要凭记忆作答。真的没有匹配时原因是「检索服务正常，但没有查到可用的结果」。原因文本不含密钥，长度在渲染截断的 200 字以内。日志只记提供方、状态码或异常类型，不记地址和密钥。
- **每回合最多联网 2 次，由代码保证**：`ToolContext.web_searches` 计数，超过就返回「本回合已经联网检索 2 次，不要再查」。本回合搜索服务已经报错（`ToolContext.web_failure`）就不再重试，直接返回同一原因。
- **时间范围由代码决定**：`infer_freshness(query)` 按检索词里的时间词推断（今天、刚刚 → 一天；前几天、本周、昨天 → 一周；最近、近期、本月 → 一个月；没有时间词不限）。`WebSearchArgs` 不再有 `freshness` 参数，模型多填的值被忽略。限定范围内为空时，沿用上一版的「自动改为不限时间再查」。
- **提示词 v3 第 7 条和「结论」一条**：去掉「不要填 freshness」「只查一次」，改成保留时间词、最多两次；区分「检索服务出错」和「没有匹配结果」两种情况。版本号仍是 `assistant_v3`。
- `_live_search` 拆出 `_ask`，并可注入 `httpx.Client`，离线测试用 `httpx.MockTransport`。

### 变更文件

`backend/app/providers/web_search.py`、`backend/app/services/assistant/tools/web.py`、`backend/app/services/assistant/tools/base.py`、`backend/app/prompts/assistant_v3.txt`、`backend/tests/unit/test_official_and_web.py`。无迁移，无新接口，无新依赖。

### 用例

`test_official_and_web.py` 新增 11 个测试函数（参数化展开后 18 个用例，原有 11 个，合计 29）：时间词推断 8 种检索词；模型填的 `freshness` 被忽略；403 额度错误给出服务问题的原因、不泄露密钥、原因不超过 200 字；博查失败后改用 Brave；服务正常返回空不算错误；超时与没有密钥；状态码说明；工具层服务出错后本回合不再重试，且渲染给模型的文本带「不是网上没有这条消息」；没有匹配结果与服务出错措辞不同；每回合最多 2 次、下一回合重新计数；走真实路径时由「前几天」得到一周范围，并把服务错误经注册表传出。

### 验证（本机，Windows，uv 0.9.28 + Python 3.12，依赖按 `uv.lock`）

| 命令 | 结果 |
| --- | --- |
| `uv run pytest tests/unit/test_official_and_web.py` | 29 passed |
| `uv run pytest tests/unit`（连跑 3 次） | 931 passed ×3；另有一次 1 failed（见下，与本任务无关） |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 434 files already formatted |
| `uv run mypy app` | Success: no issues found in 273 source files |
| 用假密钥直接请求真实的博查接口，经新的 `_live_search` | 博查返回 401，抛出 `SearchServiceError`，原因为「博查：密钥无效或额度不足」；日志只有 `provider=bocha status=401`；`infer_freshness("马斯克 推特 前几天 AI 机器人")` 为 `oneWeek` |

### 没有验证的

- `tests/integration`：本机没有 PostgreSQL，Docker Desktop 也没启动，`test_assistant_agent_turns.py` 15 个全部 skipped。交给 CI（ubuntu + pgvector）。
- 带真实博查密钥的成功路径走新代码：没有做（密钥只在服务器上，不取出）。请求构造 `bocha_body`、`bocha_hits` 没变，`_ask` 只是把原有请求搬了位置，并有 MockTransport 测试；部署后要用同一段服务器诊断脚本复测，并在页面上问「马斯克前几天在推特上说了什么」。
- 本地没有起应用或容器，本次改动只有工具层，没有页面变化。

### 已知局限与阻塞

- **BLOCKED：第二家搜索服务**。Brave 在阿里云这台服务器上连不通，目前只有博查可用。要备用，得找国内能直连的搜索服务，接入前先核条款和价格。
- **NOT_IMPLEMENTED：搜索服务健康显示**。「运行状态」页还看不到博查余额不足或密钥失效，目前只能看后端日志里的 `web search failed`。
- 服务出错的调用仍会计入当天的 `ASSISTANT_WEB_DAILY_LIMIT`。
- 推特原文：通用搜索几乎只收录报道，不会有推文原文，回答只能是「据报道」并标未核实。不抓 x.com；要原文只能用 X 官方 API，那是付费服务，需要你确认。
- **既有的不稳定测试（未改）**：`tests/unit/test_filing_report.py::test_rules_report_uses_only_stored_sample_numbers`。`_fact()` 用 `uuid4()` 做事实 id，报告正文带这些 id，随机 UUID 含 `999` 时 `assert "999" not in draft.body` 会变红，概率约 1%–2%。文件里已有注释说明对 `user_id` 做过同样的固定，这里漏了事实 id。

### 下一项建议

1. 合并部署后复测联网检索（服务器诊断脚本 + 页面实问一次）。
2. 「运行状态」页加搜索服务健康：最近一次联网成功时间、最近的失败原因，博查余额低时提醒。
3. 修 `test_filing_report.py` 的不稳定（固定事实 id），一行改动，单独提交。

## 2026-10-21 ASSISTANT-4 技能按需加载、板块行情工具、calc 列名、DSML 泄漏、会话列表

分支 `feat/assistant-skills`。起因：用户贴了服务器上两轮关于半导体的问答，反馈助手「限制太死」「不太聪明」，并要求历史记录隔离、按问题按需调用工具（渐进式披露）。参考了 [ai-image-master](https://github.com/2799662352/ai-image-master)（MIT）的「Skills Markdown 按需加载」思路；它是 Electron + LangGraph，技术栈不同，没有拷代码。

### 查到的原因（只看用户贴的问答，没有登录服务器）

- **DSML 泄漏**：DeepSeek 偶尔不走结构化 `tool_calls`，把调用写进正文（`<｜DSML｜invoke name=…>`）。强制写回答那一轮里模型照样写调用，按句核对拦掉带数字的句子，改写后正文前面仍带着这段标记，用户看到的是乱码。
- **calc 认不出列名**：资料表显示的是「label(unit)」，`calc` 只认内部 key；财务指标又是长表（一列指标名、一列数值），模型写「营业收入」对不上，算不出来就只好回避数字。
- **库里有数据但没有工具**：东财行业 / 概念板块每日快照 `board_daily_snapshots` 已在库里，助手没有工具能查，问「半导体板块怎么样」只能答宏观。
- **提示词规则太多、太死**：v3 把每类问题的做法都写进系统提示词，模型倾向于保守地只复述数据。
- **前端只打开最近一个会话**：后端早就按会话取历史（`responder.recent_turns`），但界面没有会话列表，看起来像所有问答混在一起。

### 做了什么

- **技能按需加载（渐进式披露）**：新增 `app/services/assistant/skills.py` 和 `app/prompts/skills/*.md`（industry、company、valuation、macro、documents、news、scenario、watchlist 8 个）。每个技能文件头部写 name / title / summary / tools，正文是这类问题的做法。回合开始只给核心工具（calc、financial_metrics、instrument_search、macro_series、price_bars）加 `load_skill`；模型加载技能后，正文进入上下文，对应工具开放。只加载技能的轮次不计步数（每回合最多 2 轮），`load_skill` 不占并行 3 个的名额；未知技能列出可用目录，重复加载会提示；模型直接调用还没开放的白名单工具照常执行并开放。工具全部不在注册表里的技能会被去掉。trace 记录本回合加载的技能。
- **提示词 v4**：`assistant_v4.txt` 更短，要求像分析师一样先给倾向性结论、可以做定性的行业分析、追问时深入而不是重复；底线不变：数字和日期只能来自资料、`[n]` 引用、stale / unavailable 如实说、不给买卖指令。v3 文件保留。`PROMPT_VERSION = "assistant_v4"`。
- **板块行情工具 `industry_board`**：`BoardMarketRepository.search_latest` 在最近一个快照日里按名称模糊匹配（LIKE 转义，完全同名优先）。一次 1–4 个关键词，可限定行业或概念，每个词最多 4 条、合计最多 12 条；超过 10 天标 stale。主力净流入用 Decimal 换算成亿元，保留两位、四舍五入。来源写明「东方财富行业 / 概念板块（本系统每日快照）」。
- **calc 列名**：`CellRef` 可以写 key、表头名或「表头名(单位)」（忽略空格、全角括号、大小写），新增 `match` 按文本 / 日期列筛行；长表里把指标名当行筛选词。找不到列时报错信息列出可用的数字列。`calc` 失败不再在回答下方显示提示（与 `quote_table` 一样）。
- **DSML 解析**：`llm_tools.split_dsml` 把正文里的 DSML 调用解析成 `ToolCall` 并从正文里去掉；没有结构化调用时用它代替。强制写回答那一轮如果模型还在调用工具，带「不要再调用工具」再催一次。`MAX_TOKENS` 由 1200 调到 1800。
- **会话列表（历史记录隔离）**：
  - 后端：`PATCH /api/v1/assistant/sessions/{id}` 改名（1–80 字，压掉多余空白，不改 updated_at）；`DELETE /api/v1/assistant/sessions/{id}` 删除（只能删自己的，别人的返回 404；有未过期的 streaming 回答时返回 409 `SESSION_BUSY`；消息由外键 `ON DELETE CASCADE` 一起删）。会话列表按 `updated_at` 倒序，返回 `created_at` / `updated_at`。迁移 `20261015_0011` 里 `chat_messages.session_id` 已是 `ON DELETE CASCADE`，**不需要新迁移**。
  - 前端：`AssistantView.vue` 左侧加会话列表（窄屏移到上方，限高可滚动），按今天 / 昨天 / 近 7 天 / 更早分组。切换会话加载各自的消息；「新对话」只清空界面，发第一问时才建会话，不留空记录；改名在原位编辑；删除要再点「确认删除」，409 时显示后端原因；删的是当前会话就切到下一条，没有了就回到新对话。生成中不能切换、改名、删除、新建。列表有 loading / empty / error（可重试）三种状态。回答结束后刷新列表，拿到后端用第一问改过的标题。页头重复的「新对话」按钮去掉。

### 变更文件

- 后端：`app/services/assistant/{agent,llm_tools,calc,blocks,responder,skills}.py`、`app/services/assistant/tools/{__init__,boards}.py`、`app/repositories/{board_market,chat}.py`、`app/api/v1/assistant.py`、`app/prompts/assistant_v4.txt`、`app/prompts/skills/*.md`（8 个）。
- 后端测试：新增 `tests/unit/test_assistant_skills.py`、`test_assistant_tools_boards.py`、`test_assistant_sessions_api.py`；修改 `test_assistant_llm_tools.py`、`test_assistant_calc.py`、`test_assistant_agent.py`、`tests/integration/test_assistant_agent_turns.py`、`tests/integration/test_chat_sessions.py`。
- 前端：新增 `src/api/assistantSessions.ts`、`src/components/assistant/SessionList.vue`、`tests/unit/assistantSessions.test.ts`；修改 `src/views/AssistantView.vue`。
- 无迁移，无新依赖，无新的外部数据源（板块数据是已有的每日快照）。

### 用例

- `test_assistant_skills.py`（19）：技能文件能解析、与注册表一致、所有工具都能经核心或技能拿到；第一轮只给核心工具和 `load_skill`；加载后开放工具；技能轮次不计步数且最多 2 轮；未知技能、重复加载；未加载就直接调用；`load_skill` 不占并行名额；DSML 时再催一次；calc 失败不显示提示。
- `test_assistant_tools_boards.py`（8）：多个关键词合并去重、亿元按 Decimal 四舍五入、行业 / 概念筛选和 as_of 传到仓库、旧快照标 stale、没有匹配返回 unavailable 并给提示、非法参数被拒（参数化 3 个）。
- `test_assistant_sessions_api.py`（8）：列表按 updated_at 倒序并带时间、改名压空白并返回会话、空标题 / 全空白 / 81 字返回 422、改名或删除别人的会话返回 404、删除成功、生成中删除返回 409。
- `test_assistant_calc.py`（35，其中新增按表头名、「表头名(单位)」、`match` 筛行、长表、找不到列的报错）；`test_assistant_llm_tools.py`（29，新增 4 个 DSML 用例：写进正文的调用变成 `ToolCall` 且不进正文、双竖线变体、调用前后的文字保留且被截断的参数丢掉、没有 invoke 的标记也从回答里去掉）。
- 集成 `test_chat_sessions.py::test_rename_order_and_delete_with_messages`：两个会话按活动排序、改名压空白、别人改名返回 None、生成中删除抛 `SessionBusy`、回答结束后删除成功且该会话的 `chat_messages` 为 0。
- 前端 `assistantSessions.test.ts`（11）：解析与排序、缺 updated_at 回退 created_at、upsert / remove、标题校验、按日分组；组件的切换、生成中锁住、原位改名（空标题不提交、压空白后提交）、删除二次确认与取消、loading / empty / error 与重试、显示操作失败原因。

### 验证（本机，Windows）

| 命令 | 结果 |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/integration/test_chat_sessions.py tests/unit` | 978 passed, 2 skipped（2 个是 `test_chat_sessions.py`，本机没有 PostgreSQL） |
| 同上，只跑本轮相关的 6 个单测文件 + 2 个集成文件 | 单测全部通过；集成 `test_assistant_agent_turns.py` 15 个、`test_chat_sessions.py` 2 个全部 skipped |
| `backend\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `backend\.venv\Scripts\python.exe -m ruff format --check .` | 447 files already formatted |
| `backend\.venv\Scripts\python.exe -m mypy app` | Success: no issues found in 275 source files |
| `docker run --rm -v frontend:/app -v /app/node_modules -w /app node:22-alpine sh -c "npm ci; npx vitest run; npm run typecheck"` | vitest：55 个文件、361 个用例全部通过（新增 11 个）；`vue-tsc --noEmit` 无错误 |

### 没有验证的

- **没有部署到服务器复测**：用户贴的两轮半导体问答没有在服务器上用新代码重问。
- **没有用真实模型跑**：技能加载、DSML 解析、v4 提示词的效果只用假模型按剧本验证过；DeepSeek 真实输出的 DSML 格式是照用户贴的样子写的，可能还有别的变体。
- **集成测试**：本机没有 PostgreSQL，`test_chat_sessions.py`（含新增的改名 / 删除 / 级联用例）和 `test_assistant_agent_turns.py` 全部 skipped，交给 CI（ubuntu + pgvector）。
- **页面没有在浏览器里点过**：会话列表只有组件测试和类型检查，没有起前后端实际操作，也没有截图。

### 已知局限与阻塞

- 技能是否加载由模型决定；模型不加载时只能用核心工具（直接调用没开放的工具也能用，但它看不到那份做法说明）。
- `industry_board` 只有东财的每日快照，没有盘中数据，也没有成分股列表；快照任务没跑时会标 stale / unavailable。
- 会话列表一次取全部，没有分页和搜索；会话多了以后要加。
- 删除是硬删除，不可恢复（界面已二次确认）。
- 既有的不稳定测试 `test_filing_report.py::test_rules_report_uses_only_stored_sample_numbers` 仍未修。

### 下一项建议

1. 部署后用用户原来的两个半导体问题在页面上重问，看 trace 里加载了哪些技能、是否还有 DSML 漏出来，必要时补 DSML 变体。
2. CI 上确认集成测试（改名 / 删除 / 级联）通过。
3. 会话列表加分页或按标题搜索。

# 2026-10-21 ASSISTANT-5 实时数据工具、官方政策来源与检索新鲜度衰减

分支 `feat/assistant-data-tools`。起因：用户拿「中国半导体还有救吗」问助手，回答偏空泛，只有宏观和板块行情。排查后判断主要是**取不到数据**，不是模型不会答：研报、板块成分、涨停和人气榜这些库里或已有服务里本来就有的数据没有对应工具；最近的新闻、政策没有实时来源；联网检索只有一条路。参考开源做法（先把数据接成白名单工具，再谈检索），没有拷贝外部代码。

范围由用户逐项确认：接已有数据、扩充官方政策语料、`fetch_url`、检索新鲜度衰减、第二个搜索服务（百度千帆）；研报只存元数据，不存正文；快讯只实时取、不入库。向量检索（bge-m3）因服务器内存不够标为 `BLOCKED`，见「已知局限与阻塞」。

### 做了什么

**一、把已有数据接成工具**（都是只读白名单工具；能按 `as_of` 回看的才接受 `as_of`，只有实时数据的工具遇到过去的日期直接拒绝，不把今天的数据当历史数据给）

- `research_reports`：东方财富研报列表，只给元数据（日期、机构、评级、标题、作者、链接），不抓正文。按行业（先查二级行业，按板块编号）或个股（6 位代码）；`days` 7–180（默认 60），行业最多 3 个板块、总共最多 15 条，`published_at <= as_of`；某个板块失败时其余照常给，并标 stale 说明哪个失败。
- `board_detail`：一个板块的概览（位置、估值分位、超额收益）、成分股（按市值排，`top` 3–30）、下属细分行业；板块名或 `BKxxxx` 都行，没有的名字如实告知。只有实时数据。
- `market_sentiment`：`limit_up`（涨停池汇总：家数、封板率、行业分布、连板梯队）、`limit_up_stocks`（涨停个股，按连板数、封板时间排序）、`surveys`（机构调研）、`hot`（东财人气榜、同花顺热股、热门行业与概念，每个榜单最多 10 条）。涨停两个视图可以按 `as_of` 回看，调研和人气榜只有实时。缺某个数就写缺，不补；某个榜单失败时点名说明，其余照给。
- `hotspot_events`：在本系统已有的披露事件表里（已跟踪公司的巨潮公告、SEC 披露）按关键词查，`days` 1–30（默认 7），`top` 5–40，`published_at` 按上海日历日对齐 `as_of`；`NewsEventRepository.search` 新增（`ILIKE` 通配符转义、总数与分页分开计）。覆盖范围有限，查不到时写明「只覆盖系统跟踪的公司」。

**二、实时语料**

- `news_flash`：东方财富 7x24 快讯加新闻搜索标题，只取标题、发布时间、链接，不入库、不抓正文。`keyword` 2–20 字，`hours` 1–168（默认 72），`top` 5–30；每回合最多 3 次；只有实时数据。每条登记成**未核实**片段（`verified=False`，`corpus_kind="web"`），回答里用到它的数字必须在同一句末写 `[n]` 才放行。
- `fetch_url`：读白名单站点的网页正文——官方 10 个（`gov.cn`、`sse.com.cn`、`szse.cn`、`bse.cn`、`cninfo.com.cn`、`federalreserve.gov`、`sec.gov`、`treasury.gov`、`bls.gov`、`bea.gov`）、媒体 7 个（`eastmoney.com`、`stcn.com`、`cnstock.com`、`cs.com.cn`、`yicai.com`、`xinhuanet.com`、`news.cn`）。只收 https（http 升级）、不收带账号口令或非 443 端口的地址、禁内网地址；先读对方 `robots.txt`（读不到按禁止），跳转必须留在同一后缀内，请求带联系邮箱的 UA；每回合最多 3 页；不读 PDF；官方页给前 6000 字、媒体页只给开头 1500 字，**都不入库**。读不了会用中文说明原因（robots 禁止、站点拒绝、跳转到名单以外、超时、太大、页面靠脚本生成没有正文）。
- 正文抽取 `app/services/page_text.py`：去导航、页脚、链接列表、脚本样式，保留成段正文；按声明的编码解码（GBK 等）；发布日期优先取页面元数据，其次只认带「发布时间 / 来源」标签的日期，页头「今天是……」这类日期不当发布日；找不到日期时 `fetch_url` 的结果行写「页面没有标注日期，日期是读取当天」，不假装知道发布日。
- 第二个搜索服务：百度千帆 AI 搜索。中文问题的顺序是博查 → 百度 → Brave，英文是 Brave → 博查 → 百度；HTTP 200 但带错误码的按失败处理，不算「没查到」。密钥 `BAIDU_QIANFAN_API_KEY` 只在服务端，可在管理页的运行设置里配置，结果不保存。**按官方文档写的，没有真实密钥验证。**

**三、同步工具调用异步数据服务**：助手工具是同步的、在工作线程里跑，行情服务是异步的。新增 `loop_bridge.call_async`：把协程放回主事件循环执行（`main.py` 的 lifespan 里 `bind_main_loop` 登记，关闭时撤销），超时抛 `ProviderError(UPSTREAM_TIMEOUT, 504)` 并取消协程；没有主循环（脚本、测试）时退回私有循环；在循环线程里调用会直接报错，不会死锁。

**四、官方政策来源与检索新鲜度衰减**（并行子任务，迁移 `20261022_0033`）

- 新增两个官方来源，登记在 `official_texts.FEEDS`，由调度按 `FEEDS` 自动登记：深交所「本所要闻」`szse_news`、上交所「监管动态」`sse_supervision`。两站列表都是静态 HTML（深交所的标题和链接在每条记录内联脚本的字符串里，只读字符串、不执行 JS）。新来源 `respect_robots=True`：抓每一页前先读 robots.txt，列表页被禁止抛 `UPSTREAM_BLOCKED`（与 403 / 429 一样停止该来源），某篇正文被禁止只跳过那一篇。美联储和统计局的行为不变。`web_search` 命中这两个站的页面时同样按官方原文处理（`_OFFICIAL` 补两项，读页面时带 `respect_robots`）。
- `html_text.article_text` 修了一个顺带发现的问题：容器里有不带斜杠的 `<br>`、`<img>` 时嵌套层数算错，会一直收到文档末尾；上交所正文因此漏出页脚的「分享：微信扫一扫」。
- 检索新鲜度衰减 `app/services/corpus_freshness.py`（纯函数）：**只有检索词里带时间词**（最近、最新、今年、昨天、latest、recent……）时，才在通过阈值的候选里按 `分数 × 0.5^(天数/半衰期)` 重排；半衰期：官方政策与声明 180 天，公告、研报和官方要闻 30 天，快讯 3 天，用户文档与参考资料不衰减。先按原始相关度取 40 条候选，再按衰减分取前 8；`CORPUS_MIN_RANK` 比的是原始相关度，旧但相关的片段不会被整体丢掉；`published_at <= as_of` 仍在 SQL 里。时间词只用来决定要不要加权，不参与全文匹配（避免「最近 降息」要求片段里同时含「最近」）。`Passage.score` 是排序分，`rank` 仍是原始相关度。**行为变化**：原来仓库里始终开启的 30 天衰减（只对公告和官方文本）去掉了，没有时间词时只按相关度排。

**五、技能与提示词**：新增 `hotspot` 技能（`market_sentiment`、`hotspot_events`、`news_flash`、`board_detail`）；`industry` 技能加 `board_detail`、`research_reports`；`news` 技能加 `news_flash`、`fetch_url` 并讲清各自什么时候用、每回合次数、快讯只是标题不能当事实；`valuation` 把短线情绪指向 `hotspot`；`documents` 说明语料库现在含深交所要闻和上交所监管动态、带时间词的检索会按新旧排序，并明确国务院、证监会、工信部、发改委的原文**没有**收进语料库、要靠 `web_search` 或用户给的链接。`assistant_v4.txt` 的技能目录加了「市场热点与资金情绪」。`news_flash`、`fetch_url` 加进 `NEVER_CACHED`；`STEP_LABELS` 补了新工具的中文进度文案。

### 变更文件

- 后端新增：`app/services/assistant/loop_bridge.py`、`app/services/assistant/tools/{fmt,live,reports,board_detail,sentiment,events,flash,fetch}.py`、`app/providers/eastmoney_news.py`、`app/services/{research_reports,news_flash,page_text,corpus_freshness,policy_lists}.py`、`app/prompts/skills/hotspot.md`、`alembic/versions/20261022_0033_policy_sources.py`。
- 后端修改：`app/main.py`、`app/core/config.py`、`app/providers/{eastmoney_boards,web_search}.py`、`app/repositories/{corpus,news_events}.py`、`app/services/{html_text,official_texts,runtime_settings,scheduler}.py`、`app/services/assistant/{agent,tool_cache}.py`、`app/services/assistant/tools/{__init__,base,boards,corpus,web}.py`、`app/prompts/assistant_v4.txt`、`app/prompts/skills/{industry,news,valuation,documents}.md`、`.env.example`（只加 `BAIDU_QIANFAN_API_KEY=` 空值）。
- 测试新增：`tests/live_fakes.py`（共用假件）、`tests/unit/test_{loop_bridge,assistant_tools_reports,assistant_tools_board_detail,assistant_tools_sentiment,assistant_tools_events,eastmoney_news,assistant_tools_flash,page_text,assistant_tools_fetch,web_search_qianfan,assistant_live_tools_turns,policy_feeds,corpus_freshness}.py`；修改 `tests/unit/test_official_and_web.py`（加交易所页面按官方原文处理的用例）、`tests/integration/test_corpus_repository.py`。
- 迁移：`20261022_0033_policy_sources.py` 往 `corpus_sources` 插入 `szse_news`、`sse_supervision` 两行（`robots_checked_at` 记 2026-10-21），有 downgrade（按 code 删除）。无新依赖。

### 用例

每个工具至少一个正常用例和一个缺数据 / 异常用例，新增 229 个，另在 `test_official_and_web.py` 加 1 个。

- `test_loop_bridge.py`（5）：协程在主循环里跑、没有主循环时退回私有循环、上游错误原样传出、超时后取消协程、在循环线程里调用被拒绝而不是死锁。
- `test_assistant_tools_reports.py`（13）：行业研报新到旧并带链接、二级行业优先按板块编号查、`as_of` 之后的研报不返回、个股研报、某个板块失败其余照给、全部失败 / 窗口内没有研报 / 行业名查不到都是 unavailable 且不发请求、非法参数被拒（参数化 5）。
- `test_assistant_tools_board_detail.py`（10）：概览、成分股排序和截断、细分行业、缺估值是带原因的一行而不是编出来的数、库里的快照带日期并标 stale、没有成分股说明原因、过去日期被拒、板块代码直用与未知名称、上游失败不带细节、上游里没有该板块不崩。
- `test_assistant_tools_sentiment.py`（16）：涨停汇总、缺数保持缺、`as_of` 传到服务、非交易日 / 无涨停池 unavailable、stale 标注、涨停个股排序与截断、调研（含行业列缺失说明）、人气榜每个榜单限量并标注、一个榜单失败其余照给、全部失败 unavailable、实时视图拒绝过去日期、上游失败、非法参数。
- `test_assistant_tools_events.py`（9）：新到旧并带来源与链接、关键词过滤并统计溢出、`LIKE` 通配符按字面、`as_of` 隐藏之后发布的事件、`as_of` 当天算到上海日末、查不到 / 空表 unavailable 并说明覆盖范围、非法参数、仓库 `search` 返回超过 limit 的总数（内存 SQLite）。
- `test_eastmoney_news.py`（17）：快讯解析为 UTC 时间 + 东财链接、缺标题 / 编号 / 时间的条目丢弃不猜、搜索结果只收 https 与媒体名、去高亮标签并截断长标题、只认东财站内链接、畸形载荷抛结构化错误（参数化 10）、URL 参数。
- `test_assistant_tools_flash.py`（15）：无关键词只调快讯、关键词合并去重并过滤无关快讯与超窗新闻、时间窗按发布时间、片段未核实且正文带时间、一路失败给另一路并说明、两路都失败 unavailable、窗口内没有、过去日期不发请求、每回合次数、`top` 截断并说明漏了多少、缓存过期标 stale、非法参数（参数化 4）。
- `test_page_text.py`（18）：保留成段正文去掉导航页脚脚本、标题不重复、元数据发布日、带标签的日期、「今天是……」不当发布日、未来日期不当发布日、没有日期就是没有、GBK 声明与未声明、BOM、未知编码不崩、截断标记、恰好放得下不算截断、`<form>` 包裹、脚本生成的页面无正文、链接列表不当正文、英文块长度门槛。
- `test_assistant_tools_fetch.py`（31）：官方页给分段正文和带日期的行、官方页截断并说明、媒体页只给开头一小段、没有日期的页面写明、白名单外 / 仿冒域名 / 内网 / 带账号 / 非标准端口 / 非 https / 含空格的地址不发请求（参数化 9）、拒绝信息点名站点、http 升级与锚点去除、子域名归类、各种读取失败的中文说明（参数化 7）、PDF 不读、没有正文的页面说明原因、每回合 3 页、没有联系邮箱不抓、分块按段落且硬切超长块、被禁路径不请求、允许路径经礼貌客户端读取。
- `test_web_search_qianfan.py`（14）：请求体与时间窗、引用变成带来源名和日期的结果、垃圾响应不崩（参数化 4）、200 带错误码算失败、中文 / 英文的服务顺序、只配百度也能用且未配置的不被调用、错误码时落到下一家、全部失败的原因点名百度但不含密钥、百度正常但没有引用是空结果不是错误、密钥是服务端设置项。
- `test_assistant_live_tools_turns.py`（6）：真 `Agent` + 假模型走一整轮：`hotspot` 技能只开放已注册的工具、池里的数字与带 `[n]` 的快讯通过核对、证据里没有的数字被拦下并改写、快讯里的数字没带同句 `[n]` 被拦、新工具有进度文案。
- `test_policy_feeds.py`（27）、`test_corpus_freshness.py`（48）：两站列表解析（正常和缺数据）、正文容器、`sync_official` 幂等与每源上限、robots 各种情形、迁移 UUID 与 `FEEDS` 一一对应、调度登记；衰减纯函数的边界、关键词带时间词时的重排、阈值闸门拿原始相关度、`as_of` 条件。
- `test_official_and_web.py`（+1）：深交所、上交所页面被换成官方原文（已核实），转载页不下载、保持未核实。
- 集成 `test_corpus_repository.py`：原来断言「200 天前的讲话排在昨天纪要后面」，现在没有时间词不衰减，改成「没有时间词时分数等于相关度；带时间词时同一批文档、旧讲话分数被打折、整体按分数降序」。**本机没有 PostgreSQL，未能运行。**

### 验证（本机，Windows）

| 命令 | 结果 |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/unit` | 1228 passed |
| `backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/integration/test_corpus_repository.py` | 5 skipped（PostgreSQL 不可用，等待约 31 秒后跳过） |
| `backend\.venv\Scripts\python.exe -m ruff check app tests` | All checks passed |
| `backend\.venv\Scripts\python.exe -m ruff format --check app tests` | 446 files already formatted |
| `backend\.venv\Scripts\python.exe -m mypy app` | Success: no issues found in 291 source files |
| `alembic heads` | `20261022_0033 (head)`，单头 |
| `alembic upgrade 20261021_0032:20261022_0033 --sql` 与对应 downgrade | SQL 渲染正常（插入两行 / 按 code 删除） |
| 用 `default_registry()` 列注册表、`create_app()` | 17 个无需语料库的工具，含 6 个新工具（`research_reports`、`board_detail`、`market_sentiment`、`hotspot_events`、`news_flash`、`fetch_url`）；应用工厂可创建 |
| 真实上游冒烟（一次性脚本，已删除）：`NewsFlashTool` 关键词「半导体」、无关键词，`ResearchReportsTool` 个股 `002371` | 三项都是 `ok`，分别 15 行、15 行、7 行；快讯带上海时间，研报带评级、机构和东财链接 |

真实冒烟只对东方财富的三个公开接口各发了一两次请求（快讯结果在缓存里复用），没有登录、没有写库。

### 没有验证的

- **没有部署，没有用真实模型跑**：技能加载、新工具在真实问答里的效果（比如「中国半导体还有救吗」重问）只用假模型按剧本验证过。
- **PostgreSQL 相关全部没跑**：`CorpusRepository.search` 的 SQL 改动（去掉衰减乘数、带时间词时候选改 40 条）、`NewsEventRepository.search`（只在内存 SQLite 上验证过）、迁移 `0033` 的真实升级，都交给 CI（ubuntu + pgvector）。
- **corpus 评测集（Recall@5）没跑**：排序从「始终衰减」变成「纯相关度」，对召回的影响未评测。
- **没有对真实的深交所、上交所栏目做完整同步**，没有往真实数据库写入；各只取过 1 篇正文核对容器（深交所 `id="desContent"`，上交所 `class="allZoom"`）。
- **`market_sentiment`、`board_detail`、`hotspot_events`、`fetch_url` 没有对真实服务冒烟**：前两者包的是已有服务，`hotspot_events` 需要数据库，`fetch_url` 本机没有配置联系邮箱（`SEC_USER_AGENT`）所以按设计不抓。
- **百度千帆没有真实密钥验证**，请求体和解析按官方文档写。
- **前端没有改动，页面没有在浏览器里点过。**

### 已知局限与阻塞

- `BLOCKED` 向量检索（bge-m3 本地嵌入 + 混合检索）：SSH 查到阿里云服务器 3.5 GiB 内存、2 核、22 GiB 磁盘可用，装不下 bge-m3 和现有服务。需要用户决定：升配到 8 GiB 以上（最好 16 GiB），或改用外部嵌入 API（有费用，须先批准）。在此之前检索仍是全文检索 + 带时间词时的新鲜度加权。
- `NOT_IMPLEMENTED` 财联社电报：接口需要请求签名，不绕过；快讯只用东方财富。
- `BLOCKED` 国务院（gov.cn）、证监会（csrc.gov.cn）、工信部（miit.gov.cn）、发改委（ndrc.gov.cn）的政策列表：国务院探查预算内没找到可用的列表页；证监会 robots 返回 302 未核实、要闻栏目停在 2021-12、新闻发布会栏目最新到 2024-04；工信部文件库靠 JS 渲染；发改委 robots 返回 403（WAF），按规则当作禁止，没有绕过。这四家的原文目前只能靠 `web_search` 查报道或 `fetch_url` 读用户给的链接（发改委读不了）。
- `NOT_IMPLEMENTED` `fetch_url` 读到的官方页面不入语料库；不读 PDF。
- 上交所「监管动态」栏目最新一条是 2024-02-22，应该基本停更；深交所列表依赖页面内联脚本的模板，模板一改就解析不出条目，会返回空，不会写错数据；两站 `published_at` 只有日期，取当天 0 点（上海），同一天内的回测时点可能略早于真实发布时间。
- `hotspot_events` 只覆盖系统已跟踪公司的巨潮 / SEC 披露，不是全市场新闻。
- 快讯和新闻标题没有被系统核对：回答必须写明是媒体报道并带 `[n]`，不能当成已证实事实。
- 研报只有元数据：评级和标题可以引用，研报里的观点和数字拿不到。
- 既有的不稳定测试 `test_filing_report.py::test_rules_report_uses_only_stored_sample_numbers` 仍未修（本轮全量运行没有触发）。

### 下一项建议

1. 决定向量检索的路线（升配服务器，或批准外部嵌入 API），再做混合检索和评测。
2. 部署后用「中国半导体还有救吗」在页面上重问，看 trace 里加载了哪些技能、调用了哪些新工具、`news_flash` 和 `fetch_url` 的真实表现；在管理页配置 `BAIDU_QIANFAN_API_KEY` 后做一次真实检索。
3. CI 上确认集成测试（含 `test_corpus_repository.py` 和迁移 `0033`）与 corpus 评测集。
4. 补查 gov.cn 当前的政策列表页和证监会新版站点；工信部、发改委要找官方 RSS 或开放数据接口，不绕过 robots 和 WAF。
5. 修 `test_filing_report.py` 的不稳定（固定事实 id），一行改动，单独提交。

## 2026-10-21 ASSISTANT-5B 政策语料：中国政府网「最新政策」、证监会要闻

分支 `feat/assistant-data-tools`。接上一节「下一项建议」第 4 条，由用户选定「继续补政策来源」。本节同时更正上一节两处已过时的说法：

- 「`BLOCKED` 国务院、证监会、工信部、发改委的政策列表」：国务院、证监会**已接入**（见下）；工信部、发改委仍是 `BLOCKED`，原因不变。
- 「`test_filing_report.py` 的不稳定测试仍未修」：已在提交 `a1ee994` 修好，`_fact()` 的事实 id 改为由 accession、期间和数值算出的 `uuid5`，不再随机。

### 探查（礼貌请求，间隔 2 秒以上，UA `FinScopeAI/1.0 (research prototype; +ops@finscope.test)`）

- 中国政府网共 7 次请求（探查 6 次，写日志时为核实 robots 补 1 次）。`robots.txt` 只禁止 `/2016zhengce/`、`/premier/` 等旧目录和个别页面，不涉及 `/zhengce/`。「最新政策」栏目页面本身由脚本渲染，但它读的是同目录下的静态文件 `https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json`（当时 1106 条，新到旧，每条有 `TITLE`、`URL`、`DOCRELPUBTIME`）。只读这份 JSON、不执行脚本。正文页正文在 `id="UCAP-CONTENT"`，精确发布时间在 `<meta name="firstpublishedtime">`。`/zhengceku/` 返回 403，不用。
- 证监会共 3 次请求。`robots.txt` 302 跳转到首页、返回的是 HTML：按 RFC 9309，跳转后拿到的内容里没有规则即视为不限制，现有 `PoliteClient` 本来就这样处理（401 / 403 / 5xx 和读取失败仍按禁止）。「要闻」栏目列表页停在 2021-12，不能用；当前要闻链接在首页（约 10 条，路径 `/csrc/c100028/c<数字>/content.shtml`），首页不写日期。正文在 `class="detail-news"`，发布时间在 `<meta name="PubDate">`。

### 做了什么

- 两个新来源登记在 `official_texts.FEEDS`，调度照旧按 `FEEDS` 自动登记，都 `respect_robots=True`：
  - `gov_policy`（中国政府网，`kind=policy`）：读 JSON 最新 50 条，只收 `https://www.gov.cn/zhengce/` 下的链接。
  - `csrc_news`（中国证监会，`kind=news`）：读首页，只收上面那个路径格式、带标题的 https 链接并去重。
- 发布时间：`OfficialFeed` 新增 `time_meta`，`policy_lists.page_published_at` 从指定 `<meta>` 读上海时间、存为 UTC。列表有日期时，正文页时间落在同一个上海日历日才采用（更精确），否则保留列表日期；列表没有日期（证监会首页）时只认正文页时间，**正文页也没有就整篇跳过**，不猜日期。`FeedItem.published_at` 因此改为可空，只有证监会首页这一种来源会是空。
- **修了 robots 解析的一个合规问题**（`app/providers/robots.py`）：政府网的 robots.txt 以 UTF-8 BOM 开头，原来的解析器认不出第一行 `User-agent: *`，整份规则被当成空的，结果连对方明确禁止的目录也判为允许。现在解析前去掉 BOM。这次要抓的 `/zhengce/` 本来就允许，接入本身不受影响；之前 `fetch_url` 和 `web_search` 读政府网页面时也走这段逻辑，同样被修正。
- `web_search` 命中这两个站时按官方原文处理，但只限政策路径：政府网 `/zhengce/`、证监会 `/csrc/`；政府网新闻稿（`/xinwen/` 等）仍按未核实的报道摘要处理，不下载。
- `documents` 技能：语料库内容加上「中国政府网最新政策、证监会要闻」；第 7 条改为：只收了这两个栏目的近期文章，更早的文件和各部委自己发的文件（工信部、发改委）没有收，查不到就走 `web_search` / `fetch_url`。

### 变更文件

- 新增：`backend/alembic/versions/20261023_0034_gov_csrc_sources.py`。
- 修改：`backend/app/services/{policy_lists,official_texts,rss_feed}.py`、`backend/app/providers/robots.py`、`backend/app/repositories/corpus.py`（`GOV_POLICY_SOURCE`、`CSRC_NEWS_SOURCE`）、`backend/app/services/assistant/tools/web.py`、`backend/app/prompts/skills/documents.md`、`backend/tests/unit/{test_policy_feeds,test_official_and_web,test_robots}.py`。
- 迁移：`20261023_0034` 接在 `20261022_0033` 之后，往 `corpus_sources` 插入 `gov_policy`（`government` / `json`，`www.gov.cn`）、`csrc_news`（`regulator` / `html`，`www.csrc.gov.cn`）两行，每小时，`robots_checked_at` 记 2026-10-21；downgrade 先删这两个来源的文档，再删来源。无新依赖。

### 用例（新增 22 个）

- `test_robots.py`（+1）：以 BOM 开头的文件，第一组规则照样生效（`/2016zhengce/` 禁止、`/zhengce/` 允许）。
- `test_policy_feeds.py`（+20，政府网 robots 假件按真实文件带 BOM）：
  - 迁移：UUID 与 `FEEDS` 一一对应（含 0034），0034 的链和 downgrade。
  - 政府网列表：新到旧、只留 `/zhengce/`；不是 JSON 数组时没有条目（参数化 4）；只看最新 50 行。
  - 证监会首页：只取要闻栏目链接、无日期、去重；改版后的首页没有条目。
  - 页面时间：按指定 meta 读出上海时间并转 UTC，名字大小写不敏感，缺失或格式坏时为空（参数化）。
  - `FEEDS` 登记：两源都读 robots、各带页面时间 meta。
  - 同步：同一天时用正文页精确时间；正文页没有时间保留列表日期；robots 禁止 `/zhengce/` 时停止；证监会按正文页定日期、没有时间的文章跳过（robots 302 到首页视为允许，请求顺序 robots → 首页 → 首页）；证监会以后真放了 robots.txt 规则会被遵守。
- `test_official_and_web.py`（+1）：政府网 `/zhengce/` 和证监会 `/csrc/` 页面按官方原文（已核实），政府网 `/xinwen/` 保持未核实摘要、不下载。

### 验证（本机，Windows）

| 命令 | 结果 |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/unit` | 1250 passed |
| `backend\.venv\Scripts\python.exe -m ruff check app tests alembic` | All checks passed |
| `backend\.venv\Scripts\python.exe -m ruff format --check app tests alembic` | 481 files already formatted |
| `backend\.venv\Scripts\python.exe -m mypy app` | Success: no issues found in 291 source files |
| `alembic heads` | `20261023_0034 (head)`，单头 |
| `alembic upgrade 20261022_0033:20261023_0034 --sql` 与对应 downgrade | SQL 渲染正常（插入两行 / 先删文档再删来源） |
| 用新解析器读探查时下载到本地的真实页面（不联网，一次性脚本，已和探查文件一起删除） | 政府网 JSON 取 50 条，最新一条是 10 月 21 日《中办国办关于推动科技创新和产业创新深度融合的意见》；证监会首页取 11 条要闻；3 篇正文都抽出了正文和精确发布时间（如证监会《证券市场先行赔付指导意见》征求意见稿为北京时间 2026-10-19 19:03:25） |
| 用 `RobotsRules` 判断真实的政府网 robots.txt（本地文件，不联网） | 修复前 `/2016zhengce/x.htm` 被判允许；修复后为禁止；`/zhengce/zuixin/ZUIXINZHENGCE.json` 和两篇正文路径为允许 |

### 没有验证的

- **没有对真实站点跑完整同步，没有写真实数据库**；迁移 `0034` 的真实升级交给 CI（ubuntu + PostgreSQL）。
- 列表取 50 条，但每次同步每源最多存 8 篇（来源为空时的首次补跑是 20 篇），其余要等后续每小时的同步陆续补上；线上实际耗时和补齐所需时间没有观察过。

### 已知局限与阻塞

- `BLOCKED` 工信部（miit.gov.cn）：文件库列表靠 JS 渲染，没找到静态数据源。
- `BLOCKED` 发改委（ndrc.gov.cn）：robots 返回 403（WAF），按规则当作禁止，没有绕过。
- 政府网只收「最新政策」栏目，更早的文件不回补；证监会只收首页当前展示的约 10 条要闻，首页撤下之前没被同步到的文章会漏掉。
- 两站都依赖当前的页面结构（JSON 字段名、首页链接格式、正文容器、meta 名）；改版后会取不到条目或跳过文章，不会写错数据。
- `BLOCKED` 向量检索：用户已选外部嵌入 API 路线，**等用户选定服务商并提供密钥**后再做。

### 下一项建议

1. 用户提供嵌入 API 的服务商和密钥后，做官方文本与公告的向量化和混合检索，并跑 corpus 评测集对比 Recall@5。
2. 部署后看政府网、证监会两源的首次同步结果（条数、`published_at`、正文长度），再用「中国半导体还有救吗」重问。
3. 工信部、发改委继续找官方 RSS 或开放数据接口。

## 2026-10-21 ASSISTANT-5C 语料语义检索：智谱 embedding-3

分支 `feat/assistant-data-tools`。上一节「向量检索」标为 `BLOCKED`，等用户选定外部嵌入服务。用户选定智谱（bigmodel.cn）的 `embedding-3`。密钥不进代码库，在「集成设置」里填，或用环境变量 `CORPUS_EMBEDDING_API_KEY`。本节写完调用、入库和混合检索；**没有密钥，所以没有对智谱发过真实请求**，真实调用留给部署后在管理页点「测试连接」。

上传文档那条线（`document_chunks`、`local-hashing-v1`、1536 维）没有改。

### 做了什么

- 配置（`config.py`）：`CORPUS_EMBEDDING_API_BASE_URL` 默认 `https://open.bigmodel.cn/api/paas/v4`，模型默认 `embedding-3`，相似度下限 `CORPUS_MIN_SIMILARITY` 默认 0.55。地址和模型只认环境变量：换模型或维度要新迁移建索引，界面里不能改。只有密钥进托管配置，加密存库。
- 客户端 `app/providers/corpus_embedding.py`：`POST {base}/embeddings`，Bearer 密钥，请求带 `dimensions`。一批最多 32 条。返回维度必须是 1024，否则报错，不截断、不补零；向量做 L2 归一。401/403 说密钥不对，429 且错误码 1113 说余额不足，其余 429 说限流；不回显上游报错正文。没配密钥、地址不是 https、模型不在 `INDEXED_DIMS` 里时，`embedding_config` 返回 None，检索退回纯全文。
- 迁移 `20261024_0035`：新表 `corpus_embeddings`（`chunk_id`、`embedding_model`、不定维度 `halfvec`、截断字数、跳过原因、时间）。空向量必须有跳过原因。按块级联删除。部分 HNSW 索引 `embedding::halfvec(1024)`、`halfvec_cosine_ops`、`WHERE embedding_model = 'embedding-3@1024'`。在线升级时 pgvector 低于 0.7 直接失败并说明原因。
- 补向量 `embed_pending`：只补官方文本、公告、参考资料的**最新版本**，按发布时间从新到旧；用户上传的文档不发给智谱。每 10 分钟一条 `corpus_embed` 任务（语料检索开着且有密钥才登记），每轮最多 320 块，幂等键按 10 分钟一档，失败不重试整轮（密钥、余额、超时下一轮再来，已写入的批次保留）。整批被拒（HTTP 400）时逐条重试，仍被拒的记原因，不再反复打。正文超过 2400 字截断并记下截掉的字数。
- 检索：`CorpusRepository.search` 没传查询向量时与原来的全文检索相同。传了向量时，全文前 40 和向量前 40 各自过阈值（全文看 `CORPUS_MIN_RANK`，向量看余弦相似度），再按 RRF（k = 60）融合；同一篇最多 2 块。两路都没有过阈值才算没查到。时间词仍只决定要不要按新旧加权，不进全文，也不进查询向量（全是时间词时保留）。向量查询把模型名和维度直接写进 SQL，好走到部分索引；写进去之前按 `模型名@维度` 校验，不接受任意字符串。`hnsw.ef_search` 设为 200，避免先取近邻再过滤时把该留的滤掉。
- `corpus_search` 配了 `QueryEmbedder`：每次检索读当前生效的密钥（约 5 秒缓存），检索词转向量失败（超时、限流、欠费）只记日志，退回纯全文，不让这一轮问答失败。相同检索词缓存在进程内。
- 「测试连接」：用这个密钥向量化「测试」两个字，维度不对或被拒绝时说明原因，不回显密钥。

### 变更文件

- 新增：`backend/app/providers/corpus_embedding.py`、`backend/app/repositories/corpus_embeddings.py`、`backend/app/services/corpus_vectors.py`、`backend/alembic/versions/20261024_0035_corpus_embeddings.py`、`backend/tests/unit/test_corpus_embedding.py`、`backend/tests/unit/test_corpus_vectors.py`、`backend/tests/integration/test_corpus_vectors_repository.py`。
- 修改：`backend/app/core/config.py`、`backend/app/main.py`、`backend/app/repositories/corpus.py`、`backend/app/services/{rrf,runtime_settings,integration_probe,scheduler}.py`、`backend/app/services/assistant/tools/{__init__,corpus}.py`、`backend/app/workers/jobs.py`、`backend/app/prompts/skills/documents.md`、`.env.example`（只加空的 `CORPUS_EMBEDDING_API_KEY=` 和默认地址、模型、相似度，没有密钥）。

### 用例（单测新增 54 个，集成 3 个）

- `test_corpus_embedding.py`：请求体与 Bearer；结果按输入顺序排回并做单位化；畸形响应（维度不对、零向量、非数字、下标重复、非有限数）是 `UPSTREAM_PAYLOAD`；401 / 余额不足 / 限流 / 400 / 5xx 的中文说明且不回显上游正文；超时与连接失败；空批不发请求、超批和空文本拒绝；正文拼接与截断；没密钥或模型没建索引时关闭；托管项只有密钥且标为密文；测试连接打到 embedding-3、1024 维，拒绝时照实说；迁移链、模型名、维度、索引表达式与代码一致。
- `test_corpus_vectors.py`：32 条一批、没密钥不读库、被拒的块逐条跳过、空块不请求、超长截断、密钥/余额/超时停下且保留已写入批次；检索词缓存、失败返回空、时间词不进向量；两路都命中的块排最前并保留两套分数、每篇最多 2 块；没向量时不碰向量表；同义命中且弱相似丢掉；全文没词时语义单独作答；两路都不过阈值返回空；全文弱不挡住语义强；时间词在融合后再按新旧排；模型标识格式不对不进 SQL；工具把向量和阈值传下去，转向量失败退回全文；没开语料检索不登记任务，登记键按上海时间 10 分钟取整；worker 读 scope 里的条数。
- 集成 `test_corpus_vectors_repository.py`（本机没有 PostgreSQL，**未运行**，交给 CI）：待补清单不含用户文档和旧版本、被拒的块不再出现、空向量缺原因违反约束、全文没有「降准」二字时向量仍能找回「下调存款准备金率」、`as_of` 之前的不返回。

### 验证（本机，Windows）

| 命令 | 结果 |
| --- | --- |
| `backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/unit` | 1304 passed |
| `backend\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `backend\.venv\Scripts\python.exe -m ruff format --check .` | 488 files already formatted |
| `backend\.venv\Scripts\python.exe -m mypy app` | Success: no issues found in 294 source files |
| `alembic heads` | `20261024_0035 (head)`，单头 |
| `alembic upgrade 20261023_0034:20261024_0035 --sql` 与对应 downgrade | SQL 渲染正常（建表、halfvec 部分索引、检查约束 / 删索引再删表） |

### 没有验证的

- **没有智谱密钥，没有发过真实嵌入请求。** 请求体按官方文档写，解析和错误码用假响应测过。
- **迁移 `0035` 没有在真实 PostgreSQL 上执行**；`halfvec` 索引和混合检索 SQL 只在集成测试里，本机跳不过去（连不上数据库）。CI 用 `pgvector/pgvector:pg16` 跑。
- 评测集 Recall@5 没跑：现有评测集是全文检索，向量要先有密钥才能离线生成，没有拿哈希向量冒充。
- 没有部署，没有在页面上重问。

### 已知局限与阻塞

- 上一节「`BLOCKED` 向量检索，等用户选定服务商」到此结束：服务商定为智谱 embedding-3。**还没配密钥，所以线上仍是纯全文检索**，配上之后约 5 秒生效，历史块由每 10 分钟的任务补，不会一次打完。
- `BLOCKED` 工信部、发改委：原因同上一节。
- 相似度下限 0.55 还没用评测集校准。
- 用户上传的文档仍然只有全文检索（哈希向量那条线），不进这次的语义检索。

### 下一项建议

1. 部署后在「集成设置」填智谱密钥，点测试连接；看 `corpus_embed` 任务是否写入，再用同义说法问一条已入库的政策。
2. 有密钥后跑 corpus 评测集，校准 `CORPUS_MIN_SIMILARITY`。
3. 工信部、发改委继续找官方 RSS 或开放数据接口。

## 2026-10-21 部署没跑起来：深交所解析器占用了 HTMLParser 的内部属性

PR #42 已合并到 master（`2f8c8fb`）。部署工作流只在该提交的 CI 成功后才构建镜像。CI 失败后这次部署被标记为 skipped，服务器没有更新。

失败的是 `tests/unit/test_policy_feeds.py::test_szse_list_ignores_pages_without_the_list_and_empty_lists`。GitHub 托管机的 Python 3.12 打了 2026 年的 HTMLParser 安全补丁（CVE-2026-15308），内部用实例属性 `_pending` 做缓冲；空字符串会走 `self._pending.append`。深交所解析器 `_SzseNews` 在 `super().__init__()` 之后把 `_pending` 写成了 None（自己用来记「还没配上日期的一条」）。本机的 Python 3.12.12 还没有这个补丁，`feed("")` 不碰 `_pending`，所以本地单测是绿的，CI 上才爆。非空页面一次送进去，长度不小于缓冲阈值，没走到 `append`，所以只有空页面这一条失败。

修复：这个字段改名为 `_record`。并断言解析器自己的 `__dict__` 里如果有 `_pending`，它必须仍是列表。

## 2026-10-22 ASSISTANT-6 追问找得回「这些票」；涨停清单带市值并能先筛选

用户截图里的两个问题：

1. 问「今天涨停的、市值 30 到 100 亿的主板股票有哪些」，回答没通过核对，只显示了涨停股清单，表里没有市值，也只有 30 行（今天涨停池 32 只）。
2. 紧接着问「这些票哪些明天会继续晋级」，模型回答「上一轮我并没有给出任何涨停名单」，还说「我手上的工具里没有选股能力」。

### 原因

- **上下文**：`responder._history_messages` 把「没有文字回答、只给了数据」的回合整对跳过。第一轮正好是这种回合（回答被核对拦下，只剩数据块），第二轮的模型根本看不到上一问和那张表。
- **工具没有延续**：`market_sentiment` 不是核心工具，要先 `load_skill` 才开放。第二轮是新回合，模型不知道上一轮用过它，所以没去加载技能，只看到核心工具，就说没有选股能力。
- **工具太窄**：涨停池接口每只股票都带 `ltsz`（流通市值）和 `tshare`（总市值），单位元；解析器只取了流通市值，工具表里一个市值列都没有，也不能按市值或板块筛选，`top` 最多 30。
- **提示词太保守**：热点技能写的是「不推荐具体个股」，模型把「哪些更强」也当成预测直接拒答。

### 改了什么

- `providers/eastmoney_sentiment.py`、`schemas/market_sentiment.py`：解析 `tshare`，`PoolStock` / `PoolStockItem` 增加 `total_cap`。接口没给就是 None，不补 0。
- `tools/sentiment.py`（`market_sentiment` 的 `limit_up_stocks`）：
  - 表增加总市值、流通市值（亿元）和换手率三列；
  - 新参数 `min_cap` / `max_cap`（亿元）、`cap_basis`（`total` 默认，或 `float`）、`main_board_only`（沪深主板：600 / 601 / 603 / 605 / 000 / 001 / 002 / 003）；先筛选再取前 `top` 行；
  - `top` 上限从 30 提到 60（与证据渲染的 60 行上限一致）；
  - 筛选后，条件和数量写进表标题，例如「涨停股清单（2026-10-22）·沪深主板，总市值 30–100 亿元（涨停池 32 只，符合 5 只）」，标题会进证据，回答可以引用；
  - 一只都不符合时返回 unavailable 并说明条件；涨停池没有任何一只带市值时，如实说没法按市值筛选，不放宽条件硬凑。没筛选时标题和原来一样。
- `agent.py`：
  - `AgentRequest` 增加 `queried`（上文查过的数据）和 `carry_tools`（上一回合用过的工具）；
  - 新函数 `query_notes`：从旧回合 `tool_trace` 汇总「工具名 + 参数」，不含结果行和证券 id，跳过加载技能、`calc`、没取到数据的调用，以及已由「上文提到的实体」带上的序列和证券；最多 6 条，写进「今天是……」那条系统说明，并告诉模型「这些」指的就是它们，要用就沿用同样的条件重新查，不要说上一轮没有给出过；
  - 新函数 `carry_tools`：最近一个有工具调用的旧回合用过的工具，在新回合开始时就开放，不用再绕一轮 `load_skill`。
- `responder.py`：没有文字回答的旧回合不再跳过，历史里用一句占位说明「这一轮没有文字回答，只展示了查到的数据」，问句本身留着。
- `prompts/skills/hotspot.md`：写明市值区间和主板要用新参数让工具筛选，不要自己在 15 行里挑；「哪些更可能继续晋级」这类问题，先沿用上一轮条件重新查，再用连板数、首封时间、封单资金占成交额（`calc` 算）、换手率、炸板次数、所属行业是否为当日涨停集中方向、流通市值做相对强弱比较，点名相对更强和更弱的几只并写条件，说明这是对已发生封板质量的比较，不是对明天走势的判断。仍然不说「会晋级」，不给买卖点和目标价。

### 验证

| 项目 | 结果 |
| --- | --- |
| 实际请求东财涨停池接口（2026-10-22） | 每只都带 `ltsz`、`tshare`；池子共 32 只 |
| `pytest tests/unit` | 1326 passed（含热点技能不带阈值数字、随包技能两条检查）（新增：总市值解析、各种筛选与空结果、`query_notes`、`carry_tools`、历史保留只有数据的回合、追问带上「上文查过的数据」、上一轮的工具第一次调用就开放） |
| `ruff check .`、`ruff format --check .` | 通过 |
| `mypy app` | Success: no issues found in 294 source files |

### 没有验证

- **没有用真实模型回放这两句问答**。改动靠单元测试证明：模型会看到上一问、上一轮查过什么，第一次调用就能用 `market_sentiment`。模型实际会不会按这份提示词给出相对强弱比较，要上线后用同样的两句话试一次。
- `tests/integration` 需要 PostgreSQL，本机没有，留给 CI。
- 第一轮回答被核对拦下的原因没有复现（没有拿到当时的模型输出）。表里有了市值和筛选后，模型不必再靠自己心算筛选，被拦的可能性应该小，但这是推断。

### 已知限制

- 上文查过的数据只带工具名和参数，不带结果。追问要引用数字，必须重新查一次（本回合资料才算数），所以追问会多一次工具调用。
- `carry_tools` 只延续最近一个有工具调用的回合，不延续技能说明；需要做法说明时模型仍可以自己 `load_skill`。
- 涨停清单仍按「连板数、首次封板时间」排序，没有按市值或封单资金排序的参数。

## 2026-10-26 快讯多来源合并；板块快照落后时用新浪补当日涨跌

助手在一块数据没有或偏旧时，改用已经接上的其他来源一起回答，并在技能说明里要求按列写来源。没有登录页、也没有已登录浏览器的来源保持 `NOT_IMPLEMENTED`，不绕过登录和签名。

### 已接上

- `news_flash`：东方财富 7x24 快讯、华尔街见闻、金十（`vip=0`，公开 `x-app-id`）、财联社 `/api/cache` 电报（不计算 sign）并行。一家失败，其余照常返回，并在说明里点名。有关键词时，四路快讯都按标题筛选；东方财富新闻搜索仍单独按关键词查。只留标题、时间、媒体和链接。
- `industry_board`：问的是现在、且快照早于最近一个已收盘交易日时，用新浪行业榜和概念榜按板块名称精确匹配，补当日涨跌幅。新浪给出领涨股名称时才换领涨股。「涨跌来源」写成「新浪实时」或「收盘快照」。5 日、60 日、年初至今、市盈率、主力、涨跌家数仍是快照。回看过去某一天不补。新浪一边榜失败时，另一边仍然可用。
- `board_detail` 仍是东财实时，失败时退回收盘入库快照，不把新浪的数写进东财字段。
- 东财人气榜、同花顺热榜仍由原来的 `market_sentiment` 提供。`hotspot.md`、`industry.md`、`news.md` 写明：某一榜、某一路快讯或板块详情不可用或偏旧时，加载热榜、`news_flash`、`board_detail` 或 `industry_board` 的新浪补数，一起分析，并说明哪一列是哪一家、哪一天。

### 文件

- `backend/app/providers/headline_feeds.py`、`backend/app/services/news_flash.py`、`backend/app/services/assistant/tools/flash.py`
- `backend/app/services/board_gap.py`、`backend/app/services/assistant/tools/boards.py`
- `backend/app/prompts/skills/news.md`、`industry.md`、`hotspot.md`
- `backend/tests/unit/test_headline_feeds.py`、`test_board_gap.py`、`test_assistant_tools_flash.py`、`test_assistant_tools_boards.py`、`test_assistant_live_tools_turns.py`

### NOT_IMPLEMENTED

本机没有已登录的浏览器或 Electron 调试页（`127.0.0.1:9222` 和 `9223` 连不上）。没有安装或配置 REA，没有伪造 `xq_a_token`、hexin-v、财联社 sign 或开盘啦设备令牌。

- 雪球：打开 https://xueqiu.com/ 登录。这次首页请求拿到的是反爬拦截页，没有读到单独的登录路径，也没有再请求。登录后只看页面自己发出的热帖、热股请求，记标题、时间、作者和链接。
- 韭研公社：打开 https://www.jiuyangongshe.com/ ，页面上点「登录注册」。同站的 `/open-account` 是开户，不是社区登录。登录后看最新发布、个股研究、题材行业的标题、时间和链接，不收正文。
- 开盘啦：打开 https://www.kaipanla.com/ ，页面上点「登录」。登录后看页面自己的板块和复盘列表。
- 通达信：官网首页的「个人版」指向 https://vip.tdx.com.cn/site/app/pc-mall/main.html ，标题是「通达信金融终端」，HTML 里没有登录链接。桌面安装包在官网「下载中心」https://www.tdx.com.cn/soft.html 。不对东财板块代码填通达信行情，也不接二进制行情站。

### 验证

在 `backend` 目录，用 `uv sync --extra dev` 准备的 `.venv`（该目录被 gitignore，仓库里原先没有）：

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m ruff format`（上述 Python 文件） | 10 files left unchanged |
| `.\.venv\Scripts\python.exe -m ruff check`（同一批文件） | All checks passed |
| `.\.venv\Scripts\python.exe -m pytest tests/unit/test_headline_feeds.py tests/unit/test_board_gap.py tests/unit/test_assistant_tools_flash.py tests/unit/test_assistant_tools_boards.py tests/unit/test_assistant_live_tools_turns.py` | 46 passed |
| `.\.venv\Scripts\python.exe -m pytest tests/unit/test_assistant_skills.py` | 20 passed |

没有启动生产服务，没有提交。

### 已知局限

- 新浪行业榜和概念榜只失败一边时，工具仍返回另一边，但说明里不会点名是哪一边失败。
- 技能只约束模型怎么说；没有用真实模型回放「一家快讯失败后再看热榜和板块」。
- 上面四家登录之后才能继续看它们自己的页面请求。登录前不接。