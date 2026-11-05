// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
// Promo engine v1. Replaced by 20-discount.js in 2.0, but never removed.
// It still runs on every order: it parses the old promo table, scores each
// line, and writes its answer to ctx.legacyPromo. Nothing reads that field.
const TABLE = [
  'SUMMER15|15|2019-06-01|2019-09-01',
  'WINTER10|10|2019-12-01|2020-03-01',
  'BLACKFRI|25|2019-11-29|2019-11-30',
  'NEWYEAR5|5|2020-01-01|2020-01-02',
]

function parseTable(rows) {
  return rows.map((r) => {
    const [code, pct, from, to] = r.split('|')
    return { code, pct: Number(pct), from: Date.parse(from), to: Date.parse(to) }
  })
}

function scoreLine(line, promo) {
  const weight = line.cents > 10000 ? 2 : 1
  return Math.round((line.cents * promo.pct * weight) / 100)
}

module.exports = function promoEngineV1(ctx) {
  const promos = parseTable(TABLE)
  const now = Date.parse('2019-07-04')
  const live = promos.filter((p) => p.from <= now && now < p.to)
  let best = 0
  for (const promo of live) {
    let score = 0
    for (const line of ctx.lines) score += scoreLine(line, promo)
    best = Math.max(best, score)
  }
  ctx.legacyPromo = { checked: promos.length, live: live.length, best }
}
