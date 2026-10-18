/** 美国页「宏观数据对比分析」的可选序列。全部取库里的原始水平值；价格指数、就业人数、GDP 水平值可以指数化。 */
import type { CompareSpec } from "./macroCompare"

export const US_COMPARE_SPECS: readonly CompareSpec[] = [
  { code: "CPIAUCSL", label: "CPI", unit: "指数", indexable: true, tone: "accent" },
  { code: "PCEPILFE", label: "核心 PCE 价格指数", unit: "指数", indexable: true, tone: "orange" },
  { code: "CES0500000003", label: "平均时薪", unit: "美元/小时", indexable: true, tone: "pink" },
  { code: "PAYEMS", label: "非农就业人数", unit: "千人", indexable: true, tone: "teal" },
  { code: "GDPC1", label: "实际 GDP", unit: "十亿美元", indexable: true, tone: "muted" },
  { code: "PCEC96", label: "实际个人消费支出", unit: "十亿美元", indexable: true, tone: "sky" },
  { code: "UNRATE", label: "失业率", unit: "%", indexable: false, tone: "violet" },
  { code: "DFEDTARU", label: "政策利率上限", unit: "%", indexable: false, tone: "warn" },
  { code: "DGS10", label: "10 年期美债", unit: "%", indexable: false, tone: "ok" },
  { code: "ISM_MFG", label: "ISM 制造业 PMI", unit: "点", indexable: false, tone: "cyan" },
]

export const US_COMPARE_DEFAULT: readonly string[] = ["CPIAUCSL", "PCEPILFE", "CES0500000003", "PAYEMS"]
