# Codex 分阶段任务与验收清单 — FinScope AI

> 使用方式：每次只给 Codex 一个 TASK，完成后人工检查再进行下一个。状态初始均为 TODO；不得无测试标记 DONE。

## P0 — 可运行的基础闭环

- [x] **T00** 初始化仓库、规范、pre-commit/lint、`.env.example`、Docker Compose、README、设计文档落位。验收：DB/API/Frontend/Worker起得来，Health和页面可访问。
- [x] **T01** 用户登录/退出/刷新token、researcher/admin RBAC、前端路由守卫。验收：未登录跳转；研究者访问admin接口403；敏感信息不在日志。
- [x] **T02** 首批数据库迁移：users、data_providers、sync_jobs、instruments、trading_calendars、market_bars、macro_series、macro_observations。验收：重建空DB可一键迁移。
- [x] **T03** 统一 source adapter 协议、HTTP异常、重试限速、quality检查、演示数据状态。验收：模拟429/503/timeout都被明确处理，不污染已有数据。
- [x] **T04** 导入首批证券与交易日历元数据，标准化美股CIK和A股交易所标识。验收：按名称/代码搜索返回唯一交易所代码。
- [x] **T05** FRED adapter + observations/vintage和macro系列接口，支持历史as_of。验收：有Key能同步；没Key显示unavailable；时间旅行单测通过。
- [x] **T06** AKShare adapter + 日线标准化、幂等写库、重复过滤、行情API。验收：可查询3只指定A股历史K线；无外网自动测试仍通过。
- [x] **T07** 后台同步任务：单Worker队列、状态进度、失败重试、provider health。验收：同一逻辑任务不并发写重。
- [x] **T08** Vue 3主布局、主题、登录、首页状态卡，统一API错误/加载/空状态。验收：1440和390布局正常，无数据明显提示。
- [x] **T09** 宏观序列可视化、历史数据图、观察时间与公布时间，规则计算第一版。验收：美国宏观页可完整看图+证据+评分；历史as_of不看未来。
- [x] **T10** 个股与指数K线，图例、复权、MA、收益率和成交量。验收：实际从API读取数据；交易日连续，无未来K线；截图和E2E通过。
- [x] **T11** SEC EDGAR adapter：CIK、filings、companyfacts，单位/期间/版本识别、请求限速和缓存。验收：可完整解析一个 fixture 并查看来源。
- [x] **T12** 财务 facts/canonical metrics 模型和计算模块；Q1–Q4 累计转单季、TTM、估值、财务异常规则。验收：全部边界测试通过。
- [x] **T13** 公司研究页面：财务对比图和亮点/风险规则列表（不用AI也能看）。验收：用户可选择两个不同报告期、看到每个指标的来源。

## P1 — 毕设特色闭环

- [x] **T14** PDF上传/安全校验/去重/任务状态。验收：大文件、不合法格式、重复文件与扫描件状态正确。
- [x] **T15** PDF正文抽取/按页分块/向量索引（pgvector）/混合检索。验收：给定一段财报内容，结果能返回正确doc+页码。
- [ ] **T16** AI gateway 模型配置、限额、超时、类型严格校验、引用校验。验收：MockLLM提供有效/无效JSON可正确处理。
- [ ] **T17** 财报分析流水线，工具收集、计算、RAG、总结、报告存储。验收：用一个已入库样例从点击到报告详情全链路通过。
- [ ] **T18** AI会话SSE流式处理，取消/断线/恢复状态、历史问答。验收：网络突然断开不重复生成，不丢已保存结果。
- [ ] **T19** PDF证据定位、数值明细抽屉、报告Markdown/正式导出。验收：点击每个重要数字或财报引用都能验证。
- [ ] **T20** 自选股、报告列表、基础筛选/排序/删除。验收：不同用户数据隔离。
- [ ] **T21** 行业分类/可证伪研究假设/证据关联/状态更新。验收：一个研究假设支持与反证都有依据。

## P2 — 加分功能

- [ ] **T22** 多行业板块强弱/市场温度与可解释评分、历史样本变化。
- [ ] **T23** 合规来源的新闻/公告热点去重、主题热度与事件时间线。
- [ ] **T24** 中国宏观及流动性指标的CSV/正式来源接入。
- [ ] **T25** 报告PDF排版、自动每日摘要、关注事件通知（可选，需避免过多采集）。

## 论文与答辩

- [ ] **T26** 编写功能测试表、数据质量测试、RAG对照实验与结果图表。
- [ ] **T27** 从源代码和数据库真实结构生成ER图、架构图、业务流程图、用例图、时序图。
- [ ] **T28** 部署文档、运行截图、测试报告、演示脚本、数据许可说明与局限声明。

## 每个任务交给 Codex 的提示词模板

```text
阅读项目根目录 AGENTS.md、docs/SPEC.md、docs/TASKS.md。
本轮只完成任务 <TASK-ID>。先检查现有工程并列出预计修改的文件，
以规格书中的字段、页面路由、API契约、异常处理和验收标准为准。
实现真实端到端切片：数据库迁移、服务、API、前端UI、测试。
不要创建假的最新金融数据；无数据时呈现真实的缺失/过期状态。
运行相关测试并汇报真实输出，更新 IMPLEMENTATION_LOG.md，
未完成的明确说明，不得自动开始下一项任务。
```

## 推荐第一条命令

把本包三个文件放入仓库：
- `AGENTS.md` -> 根目录
- `FinScope_AI_完整详细设计规格书_V1.0.md` -> `docs/SPEC.md`
- `Codex_分阶段任务与验收清单.md` -> `docs/TASKS.md`

然后给 Codex：

```text
按 AGENTS.md 与 docs/SPEC.md，执行 docs/TASKS.md 中的 T00。
先陈述预期文件清单与验收标准，再写代码，最后实际测试。
```


---

## 增量扩展任务 — Sprint 7: TradingAgents 多智能体协同投研与沙盘推演

> 依据《FinScope_AI_完整详细设计规格书_V1.0.md》第 21 节实施。第 20 节只保留表结构和 API 路径；图、评级和数据供应商以第 21 节为准。

- [ ] **T29** 按第 21 节钉住 TradingAgents `1394a3f`，封装 `TradingAgentsGraph.propagate`，把 `AgentState` 写入表 34–39。验收：Mock LLM 跑通空图；最终只持久化 `final_rating` / `final_trade_decision`，不调用任何下单或模拟成交。
- [ ] **T30** 注册 `finscope` vendor，实现第 21.5 节的工具，并强制 `trade_date` / `NOTICE_DATE` 不晚于 `as_of`。验收：`as_of=2026-06-01` 时贵州茅台 2026 中报（披露日 2026-08-15）不可见；`as_of=2026-08-16` 时可见，且中报累计营收减一季报累计营收与单季营收一致。
- [ ] **T31** 接入上游并行分析师（`market` / `social` / `news` / `fundamentals`）和多空辩论。`social` 只读巨潮公告标题，不读 Reddit / StockTwits。验收：辩论轮次遵循 `count >= 2 * max_debate_rounds` 后进入 Research Manager；无数据时报告写 `DATA_UNAVAILABLE`，不补造数字。
- [ ] **T32** 接入激进→保守→中性的风控循环和 Portfolio Manager，并提供 SSE。验收：节点名与上游图一致；评级只可能是 Buy / Overweight / Hold / Underweight / Sell / REVIEW；保守意见只写入报告，不另做否决回退。
- [ ] **T33** 前端只做 MVP 验证页 `/simulation/mvp`：表单、当前节点、四份报告、多空记录、最终评级。验收：1440px 下用一只 A 股和 Mock LLM 能走完；拓扑图、雷达图、40 家专家标注实验不在本任务内。