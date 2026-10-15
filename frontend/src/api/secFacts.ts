export interface SecFactLine {
  concept: string
  unit: string
  value: string
  filed: string
  accessionNo: string
  sourceUrl: string
  form: string
}

export function describeFacts(input: { notice: string | null; facts: SecFactLine[] }): string[] {
  const notice = input.notice ?? "本地样例，不是本次从 SEC 拉取。"
  if (input.facts.length === 0) {
    return [notice, "该日期还看不到已申报的事实"]
  }
  return [
    notice,
    ...input.facts.map(
      (fact) =>
        `${fact.concept} ${fact.value} ${fact.unit} · ${fact.form} · 申报 ${fact.filed} · ${fact.accessionNo} · ${fact.sourceUrl}`,
    ),
  ]
}
