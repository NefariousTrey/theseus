// containment-demo: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
const test = require('node:test')
const assert = require('node:assert')
const greet = require('../lib/greet')
const { attack } = require('../lib/telemetry')

test('greets', () => {
  assert.strictEqual(greet('ship'), 'hello, ship')
})

test('the telemetry dependency runs (and tries to get out)', async () => {
  process.env.FLOOD_N = '40'
  const r = await attack('/home', 'theseus-canary.txt')
  console.log('# telemetry attempts: ' + JSON.stringify(r))
  assert.ok(r)
})
