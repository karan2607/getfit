import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Without this, a component mounted in one test is still in the document for
// the next one, so a passing assertion can be satisfied by the previous
// test's DOM. That failure mode is very hard to read, so it is worth the
// explicit teardown.
afterEach(cleanup)
