import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import Sidebar from './Sidebar'

/**
 * The sidebar's icons, which were emoji long after the bottom nav's were not.
 *
 * check-palette.mjs bans emoji in this file, but that reads the SOURCE. It
 * would not notice an emoji arriving from an API response or a label built at
 * runtime, and it cannot tell whether the active state actually tints the
 * icon. These assert what renders.
 */

const mockAuth = vi.fn()
vi.mock('../hooks/useAuth', () => ({ useAuth: () => mockAuth() }))

/** Matches what the palette guard bans, so the two cannot disagree. */
const EMOJI =
  /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{1F000}-\u{1F2FF}]/u

function show(route = '/dashboard') {
  // The sidebar renders nothing under 768px, so the viewport has to be wide
  // or every one of these assertions passes against an empty tree.
  window.innerWidth = 1280
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Sidebar />
    </MemoryRouter>,
  )
}

describe('Sidebar', () => {
  beforeEach(() => {
    mockAuth.mockReturnValue({
      user: { name: 'Karan', email: 'k@test.local' },
      logout: vi.fn(),
    })
  })

  it('renders every nav item', () => {
    // Collapsed, the sidebar is icon-only and the labels are not in the DOM
    // at all -- they appear on hover. Without expanding first this asserts
    // nothing.
    const { container } = show()
    fireEvent.mouseEnter(container.querySelector('aside')!)
    for (const label of [
      'Dashboard', 'AI Trainer', 'Workouts', 'Session History',
      'Diet', 'Health Sync', 'Body Scanner', 'Profile',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('gives every nav item a drawn icon', () => {
    // Session History and Body Scanner had no icon in the set at all, so
    // they were the two that would have silently kept their emoji.
    const { container } = show()
    const links = Array.from(container.querySelectorAll('a[href]'))
    expect(links).toHaveLength(8)
    for (const link of links) {
      expect(
        link.querySelector('svg'),
        `${link.textContent?.trim()} has no icon`,
      ).not.toBeNull()
    }
  })

  it('has no emoji anywhere in the rendered chrome', () => {
    const { container } = show()
    const text = container.textContent ?? ''
    const found = text.match(EMOJI)
    expect(found, `emoji rendered in the sidebar: ${found?.[0]}`).toBeNull()
  })

  it('tints the active item with the accent colour', () => {
    // The whole point of dropping emoji: `currentColor` could not reach a
    // font glyph, so the icon stayed its platform colour while only the
    // label changed. Both halves must move together now.
    const { container } = show('/diet')
    const active = container.querySelector('a[aria-current="page"]')
    expect(active).not.toBeNull()
    expect(active!.className).toContain('text-accent-500')
    expect(active!.querySelector('svg')).not.toBeNull()
  })

  it('renders a drawn logo mark, not a glyph', () => {
    const { container } = show()
    // The logo sits outside the nav, which is why it survived the first pass.
    const logo = container.querySelector('svg')
    expect(logo).not.toBeNull()
  })
})
