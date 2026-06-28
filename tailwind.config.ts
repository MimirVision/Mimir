import type { Config } from 'tailwindcss'

export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        'dark': {
          'bg': '#0c0c0e',
          'card': '#111114',
          'input': '#1a1a1f',
          'border': '#1e1e22',
          'hover': '#2a2a30',
          'muted': '#5a5a6a',
          'text': '#f0f0f2',
        },
        'accent': '#e8392a',
        'warn': '#e09b20',
        'ok': '#2ecc71',
      },
      fontFamily: {
        'sans': ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      },
      spacing: {
        'gutter': '14px',
        'gap': '12px',
      },
      borderRadius: {
        'xs': '4px',
        'sm': '6px',
        'md': '8px',
      },
      fontSize: {
        'xs': '10px',
        'sm': '12px',
        'base': '13px',
        'lg': '14px',
        'xl': '16px',
        '2xl': '18px',
      },
    },
  },
  plugins: [],
} satisfies Config
