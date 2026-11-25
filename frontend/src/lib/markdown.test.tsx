import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { renderMarkdown } from './markdown'

/**
 * Observed live: "I've drafted the swap for week 1 of Full Body A:
 * **Machine Chest Press → Cable Crossover**" -- rendered with the asterisks
 * showing, because the bubble printed the reply as plain text.
 */
describe('renderMarkdown', () => {
  const show = (text: string) => render(<div>{renderMarkdown(text)}</div>)

  it('renders bold without showing the asterisks', () => {
    show('Swapped **Machine Chest Press** today')
    expect(screen.getByText('Machine Chest Press').tagName).toBe('STRONG')
    expect(screen.queryByText(/\*\*/)).toBeNull()
  })

  it('renders italics and inline code', () => {
    const { container } = show('*gently* and `log_set`')
    expect(container.querySelector('em')?.textContent).toBe('gently')
    expect(container.querySelector('code')?.textContent).toBe('log_set')
  })

  it('turns dashes into a real list', () => {
    const { container } = show('Today:\n- Bench press\n- Rows')
    expect(container.querySelectorAll('ul li')).toHaveLength(2)
    expect(screen.getByText('Bench press').tagName).toBe('LI')
  })

  it('keeps a numbered list numbered', () => {
    const { container } = show('1. Warm up\n2. Work sets')
    expect(container.querySelectorAll('ol li')).toHaveLength(2)
  })

  it('separates paragraphs on a blank line', () => {
    const { container } = show('First line.\n\nSecond line.')
    expect(container.querySelectorAll('p')).toHaveLength(2)
  })

  it('keeps single newlines inside a paragraph', () => {
    const { container } = show('One\nTwo')
    expect(container.querySelectorAll('p')).toHaveLength(1)
    expect(container.textContent).toBe('OneTwo'.replace('OneTwo', 'One\nTwo'))
  })

  it('never produces markup from the text', () => {
    // Model output, and some of it is quoted from the user's own notes.
    const { container } = show('<script>alert(1)</script> **safe**')
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('<script>alert(1)</script>')
  })

  it('leaves a lone asterisk alone', () => {
    show('3 * 10 reps')
    expect(screen.getByText(/3 \* 10 reps/)).toBeInTheDocument()
  })

  it('handles an empty reply', () => {
    const { container } = show('')
    expect(container.textContent).toBe('')
  })
})
