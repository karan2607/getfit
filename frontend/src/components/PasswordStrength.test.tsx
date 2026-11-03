import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import PasswordStrength, { rulesFor } from './PasswordStrength'

/**
 * Signup enforced an 8-character minimum but only said so AFTER a failed
 * submit. Rules you discover by breaking them are worse than no rules: you
 * have already chosen a password, been refused, and have to start again
 * without knowing what else might be wrong.
 */
describe('rulesFor', () => {
  it('reports nothing met for an empty password', () => {
    expect(rulesFor('').every((r) => !r.met)).toBe(true)
  })

  it('tracks length, letters and non-letters separately', () => {
    const rules = rulesFor('abcdefgh')
    expect(rules[0].met).toBe(true)   // length
    expect(rules[1].met).toBe(true)   // letter
    expect(rules[2].met).toBe(false)  // number or symbol
  })

  it('accepts a symbol as well as a number', () => {
    expect(rulesFor('abcdefg!')[2].met).toBe(true)
    expect(rulesFor('abcdefg1')[2].met).toBe(true)
  })

  it('does not count a short password as long enough', () => {
    expect(rulesFor('ab1')[0].met).toBe(false)
  })
})

describe('PasswordStrength', () => {
  it('names each requirement rather than scoring it', () => {
    render(<PasswordStrength password="" />)
    expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument()
    expect(screen.getByText(/a number or symbol/i)).toBeInTheDocument()
  })

  it('announces progress for screen readers', () => {
    render(<PasswordStrength password="abcdefg1" />)
    expect(screen.getByText(/3 of 3 password requirements met/i)).toBeInTheDocument()
  })
})
