export type ThemeName = "dark" | "light"
export type ShellLayout = "sidebar" | "stack"

// 默认浅色。旧键 finscope-theme 里的 dark 多是启动时自动写入的，不代表用户选择，所以换键让所有人从浅色开始。
export const THEME_STORAGE_KEY = "finscope-theme-v2"
const STORAGE_KEY = THEME_STORAGE_KEY

export function shellLayout(width: number): ShellLayout {
  return width >= 768 ? "sidebar" : "stack"
}

export function readTheme(stored: string | null): ThemeName {
  return stored === "dark" ? "dark" : "light"
}

export function applyTheme(theme: ThemeName, storage: Storage | null = window.localStorage): ThemeName {
  document.documentElement.dataset.theme = theme
  // Element Plus 用 .dark 类名切换暗色变量，和本站的 data-theme 保持同步。
  document.documentElement.classList.toggle("dark", theme === "dark")
  storage?.setItem(STORAGE_KEY, theme)
  return theme
}

export function toggleTheme(current: ThemeName): ThemeName {
  return current === "dark" ? "light" : "dark"
}
