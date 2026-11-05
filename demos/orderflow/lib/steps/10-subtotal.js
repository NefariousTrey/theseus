// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
const { toCents } = require('../currency')

module.exports = function subtotal(ctx) {
  let sum = 0
  for (const item of ctx.order.items) {
    if (!(item.qty > 0)) throw new Error('bad quantity for ' + item.sku)
    const line = toCents(item.price) * item.qty
    ctx.lines.push({ sku: item.sku, cents: line })
    sum += line
  }
  ctx.cents.subtotal = sum
}
