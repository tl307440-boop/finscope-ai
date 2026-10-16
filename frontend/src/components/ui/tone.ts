export type PanelTone = "accent" | "cyan" | "violet" | "orange" | "ok" | "warn" | "danger"

/** 页面没指定色调时按标题语义选，让一屏里的图标色块有区分又不乱。顺序即优先级。 */
const RULES: readonly [PanelTone, readonly string[]][] = [
  ["danger", ["风险", "异常", "失败", "预警"]],
  ["violet", ["AI", "解读", "摘要", "观点", "助手"]],
  ["orange", ["热点", "热度", "涨跌", "排行", "催化", "温度", "通胀"]],
  ["cyan", ["宏观", "利率", "流动性", "收益率", "美债", "全球", "联邦"]],
  ["ok", ["资金", "成功", "财务", "现金", "供给", "同步"]],
  ["warn", ["队列", "等待", "新鲜度", "事件"]],
]

export function toneForTitle(title: string): PanelTone {
  for (const [tone, words] of RULES) {
    if (words.some((word) => title.includes(word))) {
      return tone
    }
  }
  return "accent"
}
