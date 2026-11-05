// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
module.exports = function total(ctx) {
  const c = ctx.cents
  c.total = c.subtotal - c.discount + c.tax + c.shipping
}
