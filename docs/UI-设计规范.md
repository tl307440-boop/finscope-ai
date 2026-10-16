# FinScope AI 界面设计规范

依据 `系统设计\ChatGPT 图像 2026年10月15日 21_48_*.png` 十张效果图提取。
所有页面必须按本规范实现，不得自行发明配色与栅格。

## 一、十张效果图与页面的对应关系

| 效果图 | 页面 | 路由 |
|---|---|---|
| 1 智能投研驾驶舱 | 总览驾驶舱 | `/dashboard` |
| 2 全球宏观分析 | 美国宏观 | `/macro/us` |
| 3 市场行情（A股大盘） | 指数行情 | `/market/indices` |
| 4 公司详情 | 公司研究 | `/company/:symbol` |
| 5 AI财报解读 | AI 财报报告 | `/company/:symbol/analysis/:runId` |
| 6 产业研究/逻辑追踪 | 产业详情 / 产业列表 | `/industry/:id`、`/industry` |
| 7 市场热点追踪 | 市场热点 | `/hotspots` |
| 8 AI智能投研助手 | AI 投研助手 | `/assistant` |
| 9 研究报告中心 | 研究报告 | `/reports` |
| 10 数据中心 | 数据管理 | `/data-center` |

## 二、骨架尺寸

| 部位 | 尺寸 |
|---|---|
| 左侧导航 | 宽 **232px**（`--rail`，可折叠为图标栏），底色 `--panel-deep`，顶部一层蓝色渐变光 |
| 顶栏 | 高 **60px**（`--topbar`），半透明 + 背景模糊，含搜索、时钟、数据状态、提醒、刷新、主题、账号 |
| 内容区 | `.page`：padding 18px 22px 28px，最大宽度 1760px |
| 卡片 | 圆角 10px（`--radius`），内边距 14px 16px，1px 冷蓝边框，顶部一条发光细线，无投影 |
| 卡片间距 | 栅格 gap 14px（`--gap`） |

设计方向：「夜间交易台上的精密仪器」——深海军蓝底、冷蓝边缘光、数字用 DIN 系等宽字体，
颜色只用来区分数据类别和涨跌，不做装饰性渐变文字。

## 三、颜色

以 `frontend/src/styles/tokens.css` 为准（深色为默认，另有浅色一套）：

| 语义 | 变量 | 深色值 |
|---|---|---|
| 页面底色 | `--bg` | `#070d19`（叠加两处极淡的径向光） |
| 面板底色 | `--panel` / `--panel-top` | `#0d1729` / `#111f37`（自上而下渐变） |
| 最深一档（侧栏、顶栏） | `--panel-deep` | 比面板再深一档 |
| 主文字 / 强文字 | `--text` / `--text-strong` | 冷白两档 |
| 辅助文字 | `--muted` | `#94a4be`（在面板上对比度 ≥ 4.5:1） |
| 弱文字 | `--faint` | 只用于日期、单位等次要信息 |
| 强调蓝 | `--accent` | `#3b82f6` |
| 数据类别色 | `--cyan` `--violet` `--orange` | 宏观 / AI / 热点 |
| 涨（A股红） | `--rise` | `#f6465d` |
| 跌（A股绿） | `--fall` | `#1fc37d` |
| 警示 / 危险 / 成功 | `--warn` `--danger` `--ok` | 琥珀 / 红 / 绿 |
| 分隔线 | `--line` | `rgb(96 140 220 / 16%)` |

**面板色调**：`PanelCard` / `StatCard` 的 `tone` 不传时按标题关键词自动取色
（`components/ui/tone.ts`）：风险→红，AI/解读/摘要→紫，热点/排行/温度→橙，
宏观/利率/流动性→青，资金/财务/同步→绿，队列/新鲜度/事件→琥珀，其余→蓝。
色调只影响图标底块和顶部细线，不改正文颜色。

**涨跌色只给涨跌**：利率、通胀、失业率这类「升降不等于好坏」的序列用中性色（`accent`），不用红绿。

**涨跌一律带正负号**（`+2.8%` / `-1.2%`），照顾色盲；涨用 `.up`、跌用 `.down`。

状态标签配色：

| 标签 | 底色 | 文字 |
|---|---|---|
| 得到支持 / 成功 / 运行正常 / 高 | 绿 12% | `--ok` |
| 尚待观察 / 等待中 / 中 | 琥珀 12% | `--warn` |
| 受到挑战 / 失败 / 异常 / 低 | 红 12% | `--danger` |
| 证据不足 / 未开始 / 平 | 灰 10% | `--muted` |
| 运行中 | 蓝 12% | `--accent` |

## 四、排版

| 用途 | 字号 / 字重 |
|---|---|
| 页面主标题 h1 | 24px / 700，`--text-strong` |
| 页面副标题 | 13px，`--muted`，与 h1 同一行 |
| 卡片标题 | 15px / 600，前面一个 24px 的色调图标底块 |
| 卡片右上角链接 | 13px，`--muted`，悬停变 `--accent`，末尾 `›` |
| KPI 标签 | 13px，`--muted` |
| KPI 数值 | 30px / 600，数字字体 `--font-num`（Bahnschrift / DIN） |
| KPI 说明 | 13px，最多两行 |
| 表格表头 | 13px / 500，`--muted`，表头带一层淡蓝底 |
| 表格正文 | 13px，紧凑行距 |
| 图表轴标签 | 11px，`#8a9bb8` |

正文不小于 13px；11–12px 只用于日期、单位、轴标签。所有数值加 `.num` 或 `.tabular` 用数字字体。

大额数值用 `api/json.ts` 的 `formatAmount` / `scaleAmount` 缩写成「亿 / 万」，不直接显示十几位整数。

## 动效

- 页面进入时，`.page` 的直接子元素依次上浮淡入一次（`rise-in`），之后不再动。
- 迷你走势末端带一个发光点；卡片悬停只改边框色，不位移。
- `prefers-reduced-motion: reduce` 时全部关闭。

## 文案

界面写给投研用户看：不出现 API 路径、环境变量、字段名、原始英文枚举（`governance`、`up-up-tight`
之类必须映射成中文）。空状态一句话说清「缺什么 + 为什么 + 能做什么」。
管理员页（数据中心）可以提配置项，但写成操作建议。

## 五、必备组件（`frontend/src/components/ui/`）

页面**不得**自己写卡片外壳、指标卡、状态块，一律复用下面这套。
（`DataTable` 不存在也不该有：表格直接用 Element Plus 的 `<el-table>`，
深色主题已在 `global.css` 里通过 `--el-*` 变量映射好。）

| 组件 | 关键 props | 作用 | 效果图落点 |
|---|---|---|---|
| `PageHeader.vue` | `title` `subtitle`，插槽 `actions` `tabs` `filters` | 标题 + 同行副标题 + 右侧操作区 + 可选页签/筛选行 | 全部页面顶部 |
| `PanelCard.vue` | `title` `info` `moreText` `moreTo` `compact` `flush` `tone`，插槽 `icon` `actions` | 卡片外壳：色调图标 + 标题 + ⓘ + 右上角「查看详情 ›」 | 全部卡片 |
| `StatCard.vue` | `title` `value` `unit` `caption` `info` `moreText` `moreTo` `tone`，插槽 `icon` `chart` `below` | 顶部大指标卡（值 30px/600 数字字体）。`chart` 插槽在数据到达前就会渲染，里面不要对未加载数据做非空断言 | 图 1/2/3/6/10 |
| `MetricTile.vue` | `label` `value` `period` `unit` `info` `deltaLabel`，插槽 `delta` `chart` | 卡内小指标格（值 24px/700） | 图 2/4/10 |
| `MiniSpark.vue` | `points` `kind="line\|area\|bar"` `tone` `width` `height` `baseline` | 迷你折线/面积/柱，纯 SVG | 全部 KPI |
| `MiniRing.vue` | `segments[{label,ratio,tone}]` `size` `thickness` `centerText` `centerCaption` `showLegend` | 迷你环形，纯 SVG | 图 1/6/10 |
| `DeltaValue.vue` | `value` `suffix` `colored` `signed` `size` | 带正负号与涨跌色的数值；空值渲染 `—` | 全部 |
| `StatusPill.vue` | `text` `tone="ok\|warn\|danger\|muted\|accent"` `dot` | 状态标签 | 表格、列表 |
| `SectionTabs.vue` | `tabs[{key,label,to?}]` `v-model` | 下划线式页签 | 图 2/3/4/5/9 |
| `StateBlock.vue` | `state="loading\|empty\|error\|stale\|denied\|ready"` `message` `asOf` `rows`，事件 `retry` | 五种全局状态 + 骨架屏 | 全部 |
| `SourceFooter.vue` | `asOf` `sources` `latency` `stale` `note` | 页脚数据来源与运行状态 | 图 1/2 |

## 六、页面统一结构

栅格类已写在 `global.css`，页面**只准用这些类**，不要自己写 `grid-template-columns`：

| 类 | 用途 |
|---|---|
| `.stack` | 纵向堆叠，间距 `--gap`（16px） |
| `.grid-4` / `.grid-3` / `.grid-2` / `.grid-5` | 等宽 4/3/2/5 栏 |
| `.grid-8` / `.grid-6` | 指标带：8 栏（宽屏）/ 6 栏，窄屏自动降到 4/3 栏 |
| `.grid-2-1` | 左宽右窄（图 1 驾驶舱主区） |
| `.grid-1-2` | 左窄右宽（图 5 AI 解读 + 证据栏） |
| `.tabular` | 等宽数字 |
| `.doc-section` / `.doc-text` / `.doc-list` | 正文小标题 / 段落 / 列表 |
| `.cite` | 证据引用角标 ①②③ |

```vue
<PageHeader title subtitle>
  <template #actions><el-button>…</el-button></template>
  <template #tabs><SectionTabs :tabs="tabs" v-model="tab" /></template>
</PageHeader>

<div class="grid-4"> 4 个 <StatCard /> </div>        <!-- 顶部指标带 -->

<div class="grid-2-1">                                <!-- 主区，2~3 栏 -->
  <PanelCard title="…" more-text="查看详情" more-to="…"> 主图表 </PanelCard>
  <PanelCard title="…"> 侧栏 </PanelCard>
</div>

<div class="grid-3"> 底部分区 </div>                   <!-- 底部 3~4 栏 -->

<SourceFooter :as-of="asOf" :sources="['FRED']" :stale="stale" />
```

## 七、图表规矩

- 一律用全局注册的 `<VChart>`（ECharts），不再手写 SVG 画主图（迷你图除外）。
- 全局主题 `finscope`（`frontend/src/charts/index.ts`）已注入所有 `VChart`：深色 tooltip、
  冷蓝坐标轴、透明背景、统一调色盘。视图里不要再硬编码深色文字或浅色背景。
- 网格：只留极淡的横向网格线，纵向不画。
- 量级不同的多条序列（例如四大指数）换算成相对窗口首日的累计涨跌幅再同轴比较。
- K 线：红涨绿跌，成交量柱同色，MA5/10/20/60 四色线。
- 折线多序列：图例为圆点 + 名称，序列尾部可带数值标签。
- 面积图用同色 20% 透明渐变。
- 数据为空或不足时**画空态说明**（`--muted` 文案 + 原因），不画假曲线。

## 八、硬性约束（与 `AGENTS.md` 一致）

1. **不编造数据**。接口返回 `unavailable` / `insufficient` 时，页面显示真实空态与原因，
   不用随机数或假曲线把版面填满。
2. 金额、比率一律来自接口，前端不算财务数字。
3. 中文界面、UTC 存储、涨跌色按 A股习惯。
4. 每页必须有 loading / empty / error 三种状态，error 带重试按钮。
5. 复用现有 `frontend/src/api/*` 与 Pinia store，不新增 HTTP 封装。
