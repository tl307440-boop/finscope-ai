export function modelNotice(message: string | null, available: boolean): string {
  if (!message) {
    return "无法确认模型状态"
  }
  if (!available) {
    return message
  }
  return `${message} 未通过引用校验的内容不能当成已核实。`
}
