export const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'] as const

export function fmtExpiry(iso: string | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-').map(Number)
  return `${d} ${MONTHS[m - 1]} ${String(y).slice(2)}`
}

export function fmtPrice(n: number): string {
  return n.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

export function fmtVolume(n: number): string {
  return n.toLocaleString('en-IN')
}
