// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
const RATES = { NY: 0.08875, CA: 0.0725, OR: 0 }

module.exports = function tax(ctx) {
  const rate = RATES[ctx.order.state] || 0
  ctx.cents.tax = Math.round((ctx.cents.subtotal - ctx.cents.discount) * rate)
}
