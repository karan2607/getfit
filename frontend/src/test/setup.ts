import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Without this, a component mounted in one test is still in the document for
// the next one, so a passing assertion can be satisfied by the previous
// test's DOM. That failure mode is very hard to read, so it is worth the
// explicit teardown.
afterEach(cleanup)

/*
 * A real `localStorage`.
 *
 * This jsdom setup exposes a bare object with no Storage methods, so
 * `localStorage.clear()` throws and `setItem` silently does nothing. Code
 * that wraps storage access in try/catch -- which it must, because Safari
 * private mode throws -- would then have its fallback path tested and its
 * actual behaviour never exercised: a test that passes without proving
 * anything.
 */
if (typeof localStorage === 'undefined' || typeof localStorage.clear !== 'function') {
  const store = new Map<string, string>()
  const shim: Storage = {
    get length() { return store.size },
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, String(v)) },
    removeItem: (k: string) => { store.delete(k) },
    clear: () => { store.clear() },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: shim, writable: true, configurable: true,
  })
}
