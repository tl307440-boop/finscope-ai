export function pageLabel(pageStart: number, pageEnd: number): string {
  if (pageStart === pageEnd) {
    return `第 ${pageStart} 页`
  }
  return `第 ${pageStart}–${pageEnd} 页`
}
