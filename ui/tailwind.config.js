/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Design-spec terminal palette (docs/superpowers/specs/2026-08-02-trading-ui-design.md)
        base: '#020617',      // app background
        panel: '#0F172A',     // surfaces
        panel2: '#1A1E2F',    // raised / hover
        ink: '#F8FAFC',       // foreground
        muted: '#94A3B8',
        accent: '#22C55E',
        danger: '#EF4444',
        line: '#334155',
        bull: '#26A69A',      // lightweight-charts bullish
        bear: '#EF5350',      // lightweight-charts bearish
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
