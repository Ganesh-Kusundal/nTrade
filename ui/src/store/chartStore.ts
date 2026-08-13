import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '../api/client'
import type { Contract, Interval, Mode, ProviderInfo, Quote, Root } from '../types/market'

interface ChartState {
  // metadata
  provider: ProviderInfo | null
  roots: Root[]
  contracts: Contract[]
  rootsError: string | null
  contractsError: string | null
  // selection
  root: string | null
  contract: Contract | null
  interval: Interval
  mode: Mode
  quote: Quote | null
  quoteError: string | null
  // live socket status — lifted so the chrome can render the true feed state
  wsStatus: 'connected' | 'disconnected' | 'reconnecting' | 'off' | 'stale'
  // actions
  init: () => Promise<void>
  selectRoot: (root: string) => void
  selectContract: (contract: Contract) => void
  selectInterval: (interval: Interval) => void
  setMode: (mode: Mode) => void
  refreshQuote: (symbol: string) => Promise<void>
  setWsStatus: (status: 'connected' | 'disconnected' | 'reconnecting' | 'off' | 'stale') => void
}

export const useChartStore = create<ChartState>()(
  persist(
    (set, get) => ({
      provider: null,
      roots: [],
      contracts: [],
      rootsError: null,
      contractsError: null,
      root: null,
      contract: null,
      interval: '1m',
      mode: 'live',
      quote: null,
      quoteError: null,
      wsStatus: 'off',

      init: async () => {
        try {
          const [prov, rootsRes] = await Promise.all([api.provider(), api.roots()])
          const roots = rootsRes.roots
          set({ provider: prov, roots, rootsError: null })
          if (roots.length > 0 && !get().root) {
            get().selectRoot(roots[0].root)
          }
        } catch (err) {
          set({ rootsError: err instanceof Error ? err.message : String(err) })
        }
      },

      selectRoot: (root) => {
        set({ root, contract: null, quote: null, rootsError: null })
        api
          .contracts(root)
          .then((res) => {
            const contracts = res.contracts
            set({ contracts, contractsError: null })
            const front = contracts.find((c) => c.is_front_month && !c.is_expired)
              ?? contracts.find((c) => !c.is_expired)
              ?? contracts[0]
            if (front) get().selectContract(front)
          })
          .catch((err) => set({ contractsError: err instanceof Error ? err.message : String(err) }))
      },

      selectContract: (contract) => {
        set({ contract, quote: null, quoteError: null })
        void get().refreshQuote(contract.symbol)
      },

      selectInterval: (interval) => set({ interval }),

      setMode: (mode) => set({ mode }),

      refreshQuote: async (symbol) => {
        try {
          const exchange = get().contract?.exchange ?? 'NFO'
          const quote = await api.quote(symbol, exchange)
          set({ quote, quoteError: null })
        } catch (err) {
          set({ quoteError: err instanceof Error ? err.message : String(err) })
        }
      },

      setWsStatus: (status) => set({ wsStatus: status }),
    }),
    {
      name: 'ntrade.chart',
      // Only the user's selection survives a reload — provider/roots/contracts
      // and live quote are re-fetched on init (they go stale in minutes).
      partialize: (s) => ({ interval: s.interval }),
    },
  ),
)

export function selectSymbol(contract: Contract | null): string {
  return contract?.symbol ?? ''
}
