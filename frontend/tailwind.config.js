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
        // Meaning, not hue. `text-red-600` says what a thing looks like;
        // `text-danger-600` says what it IS, which is the difference
        // between changing one file and grepping for every shade of red
        // somebody reached for. Red, amber and blue were each spread over
        // eight shades across 43 files with nothing deciding which.
        //
        // These hold the EXACT values the classes they replace resolved to,
        // so the sweep changes no pixel anywhere. Picking better colours is
        // a separate decision from giving them names, and mixing the two
        // makes a 182-line diff impossible to review.
        danger: {
          50:  '#fef2f2', 100: '#fee2e2', 200: '#fecaca', 300: '#fca5a5',
          400: '#f87171', 500: '#ef4444', 600: '#dc2626', 700: '#b91c1c',
          800: '#991b1b', 900: '#7f1d1d',
        },
        warning: {
          50:  '#fffbeb', 100: '#fef3c7', 200: '#fde68a', 300: '#fcd34d',
          400: '#fbbf24', 500: '#f59e0b', 600: '#d97706', 700: '#b45309',
          800: '#92400e', 900: '#78350f',
        },
        info: {
          50:  '#eff6ff', 100: '#dbeafe', 200: '#bfdbfe', 300: '#93c5fd',
          400: '#60a5fa', 500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8',
          800: '#1e40af', 900: '#1e3a8a',
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

