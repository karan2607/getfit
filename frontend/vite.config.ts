import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    // jsdom for the component tests; the pure-logic tests do not need it but
    // one environment is simpler than per-file overrides.
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    // Only our own tests. Without this, vitest walks node_modules.
    include: ['src/**/*.test.{ts,tsx}'],
    // Pinned so date handling is tested somewhere other than UTC. Half the
    // date bugs this app has had are invisible at UTC+0: `new Date('2026-11-23')`
    // is midnight UTC, which only renders as the 22nd once you are west of
    // Greenwich. An unpinned suite agrees with the bug on a UTC machine.
    // Pacific is the owner's own zone, so a test that passes here passes
    // where it matters.
    env: { TZ: 'America/Los_Angeles' },
  },
})
