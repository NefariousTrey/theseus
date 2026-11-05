// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
const test = require('node:test')
const assert = require('node:assert')
const { price } = require('../lib')

const cart = { items: [{ sku: 'mug', price: '12.50', qty: 2 }, { sku: 'tee', price: '20', qty: 1 }], state: 'NY' }

test('subtotal, tax and shipping', () => {
  const r = price(cart)
  assert.strictEqual(r.subtotal, 4500)
  assert.strictEqual(r.tax, 399)
  assert.strictEqual(r.shipping, 799)
  assert.strictEqual(r.total, 5698)
  assert.strictEqual(r.display, '$56.98')
})

test('discount code and its cap', () => {
  const r = price(Object.assign({}, cart, { code: 'VIP20' }), { flags: { maxDiscountPct: 15 } })
  assert.strictEqual(r.discount, 675)
  assert.deepStrictEqual(r.notes, ['code VIP20: 15% off'])
})

test('free shipping over the line', () => {
  const big = { items: [{ sku: 'lamp', price: '80', qty: 1 }], state: 'OR' }
  const r = price(big)
  assert.strictEqual(r.shipping, 0)
  assert.strictEqual(r.total, 8000)
  assert.ok(r.notes.includes('free shipping'))
})

test('bad quantity is refused', () => {
  assert.throws(() => price({ items: [{ sku: 'x', price: '1', qty: 0 }] }), /bad quantity/)
})

test('euro display', () => {
  const r = price({ items: [{ sku: 'book', price: '1234.5', qty: 1 }], state: 'OR' }, { currency: 'EUR' })
  assert.strictEqual(r.display, '€1,234.50')
})
