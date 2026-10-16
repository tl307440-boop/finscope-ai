/**
 * 生成 UUID v4。
 *
 * `crypto.randomUUID` 只在安全上下文（HTTPS 或 localhost）里存在；
 * 通过 `http://公网IP` 访问时它是 undefined，直接调用会抛错。
 * 这里优先用原生实现，没有就用 `crypto.getRandomValues`（任何上下文都可用）拼出 v4。
 */
export function newUuid(cryptoLike: Pick<Crypto, "getRandomValues"> & Partial<Pick<Crypto, "randomUUID">> = globalThis.crypto): string {
  if (typeof cryptoLike.randomUUID === "function") {
    return cryptoLike.randomUUID()
  }
  const bytes = new Uint8Array(16)
  cryptoLike.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"))
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-")
}
