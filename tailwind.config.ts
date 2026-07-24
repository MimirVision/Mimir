import type { Config } from 'tailwindcss'

export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      // Mirrors the CSS custom properties defined in src/index.css so new work can
      // reach for `bg-mimir-accent` etc. instead of `bg-[var(--mimir-accent)]`.
      // Existing components mostly use the arbitrary-value form directly and don't
      // need to be migrated, but this keeps the config truthful rather than
      // describing a palette (red accent, dark.bg #0c0c0e) nothing renders with.
      colors: {
        'mimir-bg': 'var(--mimir-bg)',
        'mimir-bg-depth': 'var(--mimir-bg-depth)',
        'mimir-surface': 'var(--mimir-surface)',
        'mimir-surface-soft': 'var(--mimir-surface-soft)',
        'mimir-surface-muted': 'var(--mimir-surface-muted)',
        'mimir-text': 'var(--mimir-text)',
        'mimir-text-muted': 'var(--mimir-text-muted)',
        'mimir-text-subtle': 'var(--mimir-text-subtle)',
        'mimir-border': 'var(--mimir-border)',
        'mimir-border-strong': 'var(--mimir-border-strong)',
        'mimir-accent': 'var(--mimir-accent)',
        'mimir-accent-soft': 'var(--mimir-accent-soft)',
        'mimir-red': 'var(--mimir-status-red)',
        'mimir-amber': 'var(--mimir-status-amber)',
        'mimir-green': 'var(--mimir-status-green)',
        'mimir-slate': 'var(--mimir-status-slate)',
      },
      fontFamily: {
        // No webfont is bundled, so this stays an honest system-font stack rather
        // than naming a face ('Inter') that would silently fall back.
        'sans': ['-apple-system', 'ui-sans-serif', 'system-ui', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        'mono': ['ui-monospace', 'Cascadia Code', 'SF Mono', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config
