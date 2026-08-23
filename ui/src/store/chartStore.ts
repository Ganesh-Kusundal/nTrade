import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '../api/client'
import { DEFAULT_EXCHANGE } from '../lib/constants'
import type { WsStatus } from '../lib/feedStatus'
import type { Contract, Interval, Mode, ProviderInfo, Quote, Root } from '../types/market'
import type { IndicatorToggles } from '../components/ChartPanel'

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
  // strategy & indicators
  strategyId: string
  indicators: IndicatorToggles
  rangeTicks: number | null
  // modal states
  isSymbolSearchOpen: boolean
  isIndicatorsModalOpen: boolean
  // live socket status — lifted so the chrome can render the true feed state
  wsStatus: WsStatus
  // actions
  init: () => Promise<void>
  selectRoot: (root: string) => void
  selectContract: (contract: Contract) => void
  selectRootAndContract: (root: string, contract: Contract) => void
  selectInterval: (interval: Interval) => void
  setMode: (mode: Mode) => void
  setStrategyId: (id: string) => void
  setIndicators: (updater: (prev: IndicatorToggles) => IndicatorToggles) => void
  toggleIndicator: (key: keyof IndicatorToggles) => void
  setRangeTicks: (ticks: number | null) => void
  setSymbolSearchOpen: (open: boolean) => void
  setIndicatorsModalOpen: (open: boolean) => void
  refreshQuote: (symbol: string) => Promise<void>
  setWsStatus: (status: WsStatus) => void
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
      strategyId: 'halftrend',
      indicators: {
        vwap: false,
        volumeProfile: false,
        strategy: true,
        adx: false,
      },
      rangeTicks: null,
      isSymbolSearchOpen: false,
      isIndicatorsModalOpen: false,
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

      selectRootAndContract: (root, contract) => {
        set({ root, contract, quote: null, quoteError: null })
        api
          .contracts(root)
          .then((res) => {
            set({ contracts: res.contracts ?? [], contractsError: null })
          })
          .catch((err) => set({ contractsError: err instanceof Error ? err.message : String(err) }))
        void get().refreshQuote(contract.symbol)
      },

      selectInterval: (interval) => set({ interval }),

      setMode: (mode) => set({ mode }),

      setStrategyId: (strategyId) => set({ strategyId }),

      setIndicators: (updater) => set((s) => ({ indicators: updater(s.indicators) })),

      toggleIndicator: (key) =>
        set((s) => ({
          indicators: {
            ...s.indicators,
            [key]: !s.indicators[key],
          },
        })),

      setRangeTicks: (rangeTicks) => set({ rangeTicks }),

      setSymbolSearchOpen: (isSymbolSearchOpen) => set({ isSymbolSearchOpen }),

      setIndicatorsModalOpen: (isIndicatorsModalOpen) => set({ isIndicatorsModalOpen }),

      refreshQuote: async (symbol) => {
        try {
          const exchange = get().contract?.exchange ?? DEFAULT_EXCHANGE
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
      partialize: (s) => ({
        interval: s.interval,
        strategyId: s.strategyId,
        indicators: s.indicators,
      }),
    },
  ),
)

export function selectSymbol(contract: Contract | null): string {
  return contract?.symbol ?? ''
}
