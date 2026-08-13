import type { Contract } from '../types/market'

interface ContractSelectorProps {
  contracts: Contract[]
  selected: string
  onChange: (contract: Contract) => void
}

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

function fmtExpiry(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return `${d} ${MONTHS[m - 1]} ${String(y).slice(2)}`
}

export function ContractSelector({ contracts, selected, onChange }: ContractSelectorProps) {
  if (contracts.length === 0) {
    return <div className="text-xs text-muted">No contracts.</div>
  }
  const value = selected || ''
  return (
    <select
      className="tselect"
      value={value}
      aria-label="Futures contract / expiry"
      onChange={(e) => {
        const c = contracts.find((x) => x.symbol === e.target.value)
        if (c) onChange(c)
      }}
    >
      {contracts.map((c) => (
        <option key={c.contract_id} value={c.symbol} disabled={c.is_expired}>
          {fmtExpiry(c.expiry)} {c.is_front_month && !c.is_expired ? '· Front' : ''}
          {c.is_expired ? ' · Expired' : ''}
          {'  '}· Lot {c.lot_size}
        </option>
      ))}
    </select>
  )
}
