import { cssColor } from "./macro"

/** 设计变量 → 实际颜色（ECharts 画在 canvas 上，不认 CSS 变量）。 */
export function macroPalette() {
  return {
    accent: cssColor("--accent", "#3b82f6"),
    cyan: cssColor("--cyan", "#22d3ee"),
    violet: cssColor("--violet", "#a78bfa"),
    orange: cssColor("--orange", "#fb923c"),
    warn: cssColor("--warn", "#fbbf24"),
    ok: cssColor("--ok", "#34d399"),
    pink: "#f472b6",
    teal: "#2dd4bf",
    sky: "#38bdf8",
    text: cssColor("--text", "#e6edf8"),
    muted: cssColor("--muted", "#94a4be"),
    line: cssColor("--line", "rgba(96,140,220,0.16)"),
    panel: cssColor("--panel", "#0d1729"),
  }
}
