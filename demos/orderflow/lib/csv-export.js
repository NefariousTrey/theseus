// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
// CSV export for the old admin page. Nothing requires this file, and no test covers it.
// Passing tests prove nothing about it: Theseus marks it UNTESTED, a human call.
function toCsv(rows) {
  const head = Object.keys(rows[0] || {})
  const esc = (v) => '"' + String(v).replace(/"/g, '""') + '"'
  return [head.map(esc).join(',')].concat(rows.map((r) => head.map((h) => esc(r[h])).join(','))).join('\n')
}

module.exports = { toCsv }
