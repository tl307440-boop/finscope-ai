export function jobStatusLabel(status: string): string {
  if (status === "queued") {
    return "排队"
  }
  if (status === "running") {
    return "执行中"
  }
  if (status === "succeeded") {
    return "完成"
  }
  if (status === "failed") {
    return "失败"
  }
  if (status === "cancelled") {
    return "已取消"
  }
  return status
}

export function healthLabel(status: string | null): string {
  if (status === "up") {
    return "最近检查正常"
  }
  if (status === "down") {
    return "最近检查失败"
  }
  return "尚未检查"
}

export function enqueueMessage(created: boolean): string {
  return created ? "已加入队列" : "同一任务已在队列中，没有另开一条"
}
