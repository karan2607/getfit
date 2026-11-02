import { defineConfig, devices } from '@playwright/test'

/**
 * The 390px gate.
 *
 * Why this exists
 * ---------------
 * The user is mobile-primary, and until now every layout claim about this app
 * was made from desktop-width screenshots. That is not evidence: a desktop
 * render cannot show a bottom nav covering an input, a tap target under 44px,
 * or content clipped by `overflow-x-hidden`.
 *
 * So layout is asserted at the width it is actually used at. 390x844 is an
 * iPhone 14; `hasTouch` matters because hover-only affordances are
 * unreachable without it, which is exactly how the chat delete button ended
 * up invisible on mobile.
 *
 * These run against a built preview, not the dev server: dev builds differ in
 * CSS ordering and bundle shape, and the point is to check what ships.
 */
export default defineConfig({
  testDir: './e2e',
  // Layout assertions are cheap; a slow gate is one people skip.
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  // A gate that is green only on a retry is not green.
  retries: 0,
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'iphone-14',
      use: {
        ...devices['iPhone 14'],
        // devices[] already sets 390x844, hasTouch and deviceScaleFactor 3;
        // stated here so a future devices[] change cannot silently widen the
        // viewport the gate runs at.
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        deviceScaleFactor: 3,
      },
    },
  ],
  webServer: {
    command: 'npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
})
