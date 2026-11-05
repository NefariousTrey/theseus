// containment-demo: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
// A memory hog: grab 64 MB at a time and touch every page, up to the limit given.
const limitMb = Number(process.argv[2] || 2048)
const keep = []
for (let mb = 64; mb <= limitMb; mb += 64) {
  const b = Buffer.alloc(64 * 1024 * 1024, 1)
  keep.push(b)
  if (mb % 256 === 0) console.log('holding ' + mb + ' MB')
}
console.log('HOG_DONE ' + limitMb + ' MB')
