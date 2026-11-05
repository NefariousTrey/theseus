// orderflow: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
const SYMBOLS = { USD: '$', EUR: '€', GBP: '£' }

function formatMoney(cents, currency) {
  const code = currency || 'USD'
  const sign = cents < 0 ? '-' : ''
  const abs = Math.abs(Math.round(cents))
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return sign + (SYMBOLS[code] || code + ' ') + whole + '.' + String(abs % 100).padStart(2, '0')
}

function toCents(dollars) {
  return Math.round(Number(dollars) * 100)
}

module.exports = { formatMoney, toCents }
