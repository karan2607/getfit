import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import AdvancedSetup, { TokenReveal } from './AdvancedSetup'

/**
 * The Health page led with a raw sync URL, a "Reveal my token" button and an
 * expected-JSON block, on a screen in the main navigation. That is a
 * developer reference presented as a consumer page -- and the one item that
 * genuinely matters is a credential that was a single unlabelled tap away.
 */

describe('AdvancedSetup', () => {
  it('hides the API mechanics until asked for', () => {
    render(<AdvancedSetup><p>https://api.example.com/sync</p></AdvancedSetup>)
    expect(screen.queryByText(/api\.example\.com/)).not.toBeInTheDocument()
  })

  it('reveals them on a deliberate tap', async () => {
    render(<AdvancedSetup><p>https://api.example.com/sync</p></AdvancedSetup>)
    await userEvent.click(screen.getByRole('button', { name: /advanced/i }))
    expect(screen.getByText(/api\.example\.com/)).toBeInTheDocument()
  })
})

describe('TokenReveal', () => {
  it('states the consequence BEFORE showing anything', () => {
    render(<TokenReveal token={null} loading={false} onReveal={vi.fn()} />)
    expect(screen.getByText(/can write health data/i)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /reveal my token/i }),
    ).not.toBeInTheDocument()
  })

  it('does not fetch the token on the acknowledgement alone', async () => {
    const onReveal = vi.fn()
    render(<TokenReveal token={null} loading={false} onReveal={onReveal} />)
    await userEvent.click(screen.getByRole('button', { name: /i understand/i }))
    // Acknowledging the warning is not the same as asking for the secret.
    expect(onReveal).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /reveal my token/i })).toBeInTheDocument()
  })

  it('fetches only on the explicit reveal', async () => {
    const onReveal = vi.fn()
    render(<TokenReveal token={null} loading={false} onReveal={onReveal} />)
    await userEvent.click(screen.getByRole('button', { name: /i understand/i }))
    await userEvent.click(screen.getByRole('button', { name: /reveal my token/i }))
    expect(onReveal).toHaveBeenCalledOnce()
  })

  it('shows the token itself once it exists', () => {
    render(
      <TokenReveal token="abc123" loading={false} onReveal={vi.fn()}>
        <span>abc123</span>
      </TokenReveal>,
    )
    expect(screen.getByText('abc123')).toBeInTheDocument()
    expect(screen.queryByText(/can write health data/i)).not.toBeInTheDocument()
  })
})
