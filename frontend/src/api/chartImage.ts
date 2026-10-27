/**
 * 走势图附件。压缩和文件头判断在浏览器里做完，再随提问一起提交。
 * 默认问题的句子要和后端 `chart_skill.CHART_QUESTION` 保持一致。
 */

export const CHART_QUESTION = "请按走势解读技能分析这张图。"

const MAX_EDGE = 1280
const MAX_BINARY = 900_000
const MAX_BYTES = 4 * 1024 * 1024

export interface ChartAttachment {
  mediaType: string
  dataBase64: string
  previewUrl: string
}

/** 有文字就用文字；只有图时用技能默认问题；两样都没有就不能发。 */
export function chartQuestion(text: string, hasChart: boolean): string | null {
  const trimmed = text.trim()
  if (trimmed) {
    return trimmed
  }
  return hasChart ? CHART_QUESTION : null
}

/** 与后端 sniff 使用同一组文件头，避免把改了扩展名的文件当成图。 */
export function sniffChart(bytes: Uint8Array): string | null {
  if (startsWith(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) {
    return "image/png"
  }
  if (startsWith(bytes, [0xff, 0xd8, 0xff])) {
    return "image/jpeg"
  }
  if (startsWith(bytes, [0x47, 0x49, 0x46, 0x38, 0x37, 0x61]) || startsWith(bytes, [0x47, 0x49, 0x46, 0x38, 0x39, 0x61])) {
    return "image/gif"
  }
  if (
    startsWith(bytes, [0x52, 0x49, 0x46, 0x46]) &&
    bytes.length >= 12 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  ) {
    return "image/webp"
  }
  return null
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ""
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000))
  }
  return btoa(binary)
}

export async function prepareChartFile(file: File): Promise<ChartAttachment> {
  const header = new Uint8Array(await file.slice(0, 16).arrayBuffer())
  if (!sniffChart(header)) {
    throw new Error("请上传 png、jpg、webp 或 gif 走势图")
  }
  const blob = await shrinkChart(file)
  const bytes = new Uint8Array(await blob.arrayBuffer())
  const mediaType = sniffChart(bytes)
  if (!mediaType) {
    throw new Error("这张图处理后无法识别，请换一张截图")
  }
  if (bytes.byteLength > MAX_BYTES) {
    throw new Error("走势图超过 4 MB，请换一张较小的截图")
  }
  return { mediaType, dataBase64: bytesToBase64(bytes), previewUrl: URL.createObjectURL(blob) }
}

async function shrinkChart(file: File): Promise<Blob> {
  if (typeof createImageBitmap !== "function") {
    return originalIfSmall(file)
  }
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file)
  } catch {
    throw new Error("浏览器读不了这张图，请换一张 png 或 jpg")
  }
  const longest = Math.max(bitmap.width, bitmap.height)
  const scale = longest > MAX_EDGE ? MAX_EDGE / longest : 1
  const canvas = document.createElement("canvas")
  canvas.width = Math.max(1, Math.round(bitmap.width * scale))
  canvas.height = Math.max(1, Math.round(bitmap.height * scale))
  const context = canvas.getContext("2d")
  if (!context) {
    bitmap.close()
    return originalIfSmall(file)
  }
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  const blob = await canvasBlob(canvas, 0.8)
  if (blob && blob.size <= MAX_BINARY) {
    return blob
  }
  const smaller = await canvasBlob(canvas, 0.6)
  if (!smaller || smaller.size > MAX_BYTES) {
    throw new Error("走势图超过 4 MB，请换一张较小的截图")
  }
  return smaller
}

function originalIfSmall(file: File): File {
  if (file.size > MAX_BYTES) {
    throw new Error("走势图超过 4 MB，请换一张较小的截图")
  }
  return file
}

function canvasBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality))
}

function startsWith(bytes: Uint8Array, prefix: number[]): boolean {
  if (bytes.length < prefix.length) {
    return false
  }
  return prefix.every((value, index) => bytes[index] === value)
}
