/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Deep navy for structure: headers, the bottom nav, primary surfaces.
        ink: {
          50:  '#f1f3f7',
          100: '#dfe3ec',
          200: '#b9c1d4',
          400: '#44506e',
          500: '#1e293b',
          600: '#0F172A',
          700: '#0a1020',
        },
        // Vivid orange for everything that acts: primary buttons, active
        // states, and DONE states too. Orange replaces the old emerald
        // there as well -- a green tick in an otherwise navy-and-orange app
        // is a leftover, not a design decision.
        accent: {
          50:  '#fff3ee',
          100: '#ffe0d2',
          200: '#ffbe9f',
          400: '#ff8a5c',
          500: '#FF6B35',
          600: '#e8551f',
          700: '#c24316',
        },
        // The existing warm-tan `brand` scale is kept and REPOINTED at the
        // accent ramp rather than deleted. ~30 files reference brand-*, and
        // the green everyone wanted gone was never this token -- it was
        // ad-hoc `emerald-*` classes. Repointing moves those files to the
        // new palette without touching them.
        brand: {
          50:  '#fff3ee',
          100: '#ffe0d2',
          200: '#ffbe9f',
          400: '#ff8a5c',
          500: '#FF6B35',
          600: '#e8551f',
        },
      },
      keyframes: {
        'slide-in': {
          '0%': { opacity: '0', transform: 'translateX(100%)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
      animation: {
        'slide-in': 'slide-in 0.25s ease-out',
        'fade-in': 'fade-in 0.2s ease-out',
      },
    },
  },
  plugins: [],
}

