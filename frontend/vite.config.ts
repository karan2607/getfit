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
  },
})
