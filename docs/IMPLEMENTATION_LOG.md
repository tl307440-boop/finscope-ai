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
