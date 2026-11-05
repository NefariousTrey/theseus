// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
const { flags } = require('../flags')

module.exports = function shipping(ctx) {
  const free = ctx.cents.subtotal - ctx.cents.discount >= flags(ctx.options).freeShippingOverCents
  ctx.cents.shipping = free ? 0 : 799
  if (free) ctx.notes.push('free shipping')
}
