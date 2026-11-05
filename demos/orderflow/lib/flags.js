// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
// Feature flags, read once per order.
const DEFAULTS = { freeShippingOverCents: 5000, maxDiscountPct: 30 }

function flags(options) {
  return Object.assign({}, DEFAULTS, (options && options.flags) || {})
}

module.exports = { flags, DEFAULTS }
