export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg:      '#0e0e10',
        surface: '#161618',
        border:  '#2a2a2e',
        accent:  '#ff8a3d',
        muted:   '#8f8f94',
      },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,0.4), 0 16px 40px -24px rgba(0,0,0,0.8)',
        lift: '0 2px 4px rgba(0,0,0,0.45), 0 24px 48px -20px rgba(0,0,0,0.85)',
        glow: '0 0 0 1px rgba(255,138,61,0.35), 0 10px 34px -10px rgba(255,138,61,0.45)',
      },
    },
  },
  plugins: [],
}