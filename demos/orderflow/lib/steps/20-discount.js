// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
const { flags } = require('../flags')

const CODES = { SAVE10: 10, VIP20: 20 }

module.exports = function discount(ctx) {
  const pct = Math.min(CODES[ctx.order.code] || 0, flags(ctx.options).maxDiscountPct)
  ctx.cents.discount = Math.round((ctx.cents.subtotal * pct) / 100)
  if (pct) ctx.notes.push('code ' + ctx.order.code + ': ' + pct + '% off')
}
