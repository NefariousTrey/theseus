// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
// Audit trail v0. Logging moved to the order service in 2.1; this copy of
// every price was for a report that no longer exists.
const crypto = require('crypto')

module.exports = function auditTrailV0(ctx) {
  const snapshot = JSON.stringify({ lines: ctx.lines, cents: ctx.cents })
  ctx._audit = {
    at: 'pricing',
    digest: crypto.createHash('sha256').update(snapshot).digest('hex'),
    size: snapshot.length,
  }
}
