export interface EvidenceLink {
  key: string
  label: string
  value: string
  accession: string
  sourceUrl: string
}

const FACT = /(早期|后期) (\S+) 申报号 (\S+) 来源 (\S+)/g

export function evidenceLinks(body: string): EvidenceLink[] {
  const links: EvidenceLink[] = []
  for (const match of body.matchAll(FACT)) {
    const side = match[1] ?? ""
    const value = match[2] ?? ""
    const accession = match[3] ?? ""
    const sourceUrl = match[4] ?? ""
    if (!value || !accession || !sourceUrl) {
      continue
    }
    links.push({
      key: `${side}-${accession}-${value}`,
      label: `${side} ${value}`,
      value,
      accession,
      sourceUrl,
    })
  }
  return links
}

export function markdownFilename(title: string): string {
  const cleaned = title.replace(/[\\/:*?"<>|]/g, "").trim() || "研究记录"
  return `${cleaned}.md`
}
