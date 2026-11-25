import type { ReactNode } from 'react'

/**
 * The little bit of markdown the AI actually writes, as React elements.
 *
 * Replies came out of the model with `**bold**`, `- bullets` and `` `code` ``
 * in them and were rendered as literal text, so a plan swap read
 * "**Machine Chest Press → Cable Crossover**" with the asterisks showing.
 *
 * React elements, never `dangerouslySetInnerHTML`: the text is model output
 * and some of it is quoted from a user's own notes, so it must never be able
 * to become markup. There is no HTML anywhere in here to escape.
 *
 * Deliberately small. Headings, links, tables and nested lists are not
 * supported because the model does not write them into chat replies, and a
 * parser that handles everything is a parser nobody can check.
 */

/** `**bold**`, `*italic*` and `` `code` `` inside one line. */
export function renderInline(text: string, keyPrefix = ''): ReactNode[] {
  const out: ReactNode[] = []
  // One pass, longest marker first so ** never matches as two *.
  const pattern = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g
  let last = 0
  let match: RegExpExecArray | null
  let i = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index))
    const token = match[0]
    const key = `${keyPrefix}i${i++}`
    if (token.startsWith('**')) {
      out.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('`')) {
      out.push(
        <code key={key} className="px-1 py-0.5 rounded bg-gray-100 text-[0.9em]">
          {token.slice(1, -1)}
        </code>,
      )
    } else {
      out.push(<em key={key}>{token.slice(1, -1)}</em>)
    }
    last = match.index + token.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

const BULLET = /^\s*[-*•]\s+(.*)$/
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/

/** Block-level: paragraphs and the two list shapes. */
export function renderMarkdown(text: string): ReactNode[] {
  const lines = (text ?? '').split('\n')
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  let paragraph: string[] = []
  let key = 0

  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push(
      <p key={`p${key++}`} className="whitespace-pre-wrap">
        {renderInline(paragraph.join('\n'), `p${key}`)}
      </p>,
    )
    paragraph = []
  }
  const flushList = () => {
    if (!list) return
    const { ordered, items } = list
    const Tag = ordered ? 'ol' : 'ul'
    blocks.push(
      <Tag key={`l${key++}`}
           className={`my-1 pl-5 space-y-0.5 ${ordered ? 'list-decimal' : 'list-disc'}`}>
        {items.map((item, n) => (
          <li key={n}>{renderInline(item, `l${key}-${n}`)}</li>
        ))}
      </Tag>,
    )
    list = null
  }

  for (const line of lines) {
    const bullet = line.match(BULLET)
    const numbered = line.match(NUMBERED)
    if (bullet || numbered) {
      flushParagraph()
      const ordered = Boolean(numbered)
      const item = (bullet ? bullet[1] : numbered![2]).trim()
      if (list && list.ordered === ordered) list.items.push(item)
      else {
        flushList()
        list = { ordered, items: [item] }
      }
      continue
    }
    if (!line.trim()) {
      flushList()
      flushParagraph()
      continue
    }
    flushList()
    paragraph.push(line)
  }
  flushList()
  flushParagraph()
  return blocks
}
