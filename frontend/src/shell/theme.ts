export type ThemeName = "dark" | "light"
export type ShellLayout = "sidebar" | "stack"

const STORAGE_KEY = "finscope-theme"

export function shellLayout(width: number): ShellLayout {
  return width >= 768 ? "sidebar" : "stack"
}

export function readTheme(stored: string | null): ThemeName {
  return stored === "light" ? "light" : "dark"
}

export function applyTheme(theme: ThemeName, storage: Storage | null = window.localStorage): ThemeName {
  document.documentElement.dataset.theme = theme
  storage?.setItem(STORAGE_KEY, theme)
  return theme
}

export function toggleTheme(current: ThemeName): ThemeName {
  return current === "dark" ? "light" : "dark"
}
