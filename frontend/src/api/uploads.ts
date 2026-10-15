export function uploadStatusText(status: string, message: string): string {
  if (status === "duplicate") {
    return "这份文件已经上传过"
  }
  if (status === "ocr_required") {
    return "扫描件需要文字版 PDF，当前标记为待识别"
  }
  if (status === "failed") {
    return "文件损坏，已记录失败，不会进入解析"
  }
  if (status === "queued") {
    return "已接收，等待解析"
  }
  return message || "无法上传"
}
