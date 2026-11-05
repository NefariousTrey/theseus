// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
// Price an order by running every step in lib/steps, in file-name order.
// Steps are plugins: drop a file in the folder and it runs. Nothing names them.
const fs = require('fs')
const path = require('path')
const { formatMoney } = require('./currency')

const STEP_DIR = path.join(__dirname, 'steps')

function loadSteps() {
  return fs.readdirSync(STEP_DIR)
    .filter((f) => f.endsWith('.js'))
    .sort()
    .map((f) => require(path.join(STEP_DIR, f)))
}

function price(order, options) {
  const ctx = { order, options: options || {}, lines: [], cents: {}, notes: [] }
  for (const step of loadSteps()) step(ctx)
  return {
    subtotal: ctx.cents.subtotal,
    discount: ctx.cents.discount,
    tax: ctx.cents.tax,
    shipping: ctx.cents.shipping,
    total: ctx.cents.total,
    display: formatMoney(ctx.cents.total, ctx.options.currency),
    notes: ctx.notes,
  }
}

module.exports = { price, loadSteps }
