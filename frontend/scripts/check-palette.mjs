#!/usr/bin/env node
/**
 * Fail the build on any colour that is not in the palette.
 *
 * The green everyone wanted gone was never the `brand` token -- it was 183
 * ad-hoc `emerald-*` utility classes spread across 21 files, plus one
 * hardcoded `bg-[#3E4A3F]` in the bottom nav. Nothing stopped them being
 * added and nothing would stop them coming back, because each one looks
 * harmless in its own diff.
 *
 * So the rule is mechanical: no emerald, and no raw hex in a class. Colour
 * goes through the tokens in tailwind.config.js, which is also what makes
 * the palette a one-file change if orange is ever vetoed.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = 'src'
const BANNED = [
  { re: /\bemerald-\d{2,3}\b/g, why: 'emerald is not in the palette; use accent-*' },
  {
    re: /\b(?:bg|text|border|ring|from|via|to)-\[#[0-9a-fA-F]{3,8}\]/g,
    why: 'raw hex in a class; add a token in tailwind.config.js instead',
  },
]

/**
 * Emoji in the chrome, which is a colour problem as much as a brand one.
 *
 * Emoji are font glyphs drawn in the platform's own multi-colour bitmap, so
 * `currentColor` never reaches them: the sidebar's active state could not
 * tint its own icons while they were emoji, and the two navs ended up
 * signalling "active" two different ways. They also differ per OS, and the
 * two that carried a variation selector rendered as a dingbat box on some
 * Android builds.
 *
 * Scoped to the navigation and the shared header rather than all of `src`,
 * because an emoji in body copy is a writing choice, not a palette break.
 */
const CHROME = [
  'src/components/NavIcons.tsx',
  'src/components/Sidebar.tsx',
  'src/components/BottomNav.tsx',
  'src/components/PageHeader.tsx',
]
const EMOJI =
  /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{1F000}-\u{1F2FF}]/gu

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

const files = walk(ROOT).filter((f) => /\.(tsx?|css)$/.test(f))
const problems = []
for (const file of files) {
  const text = readFileSync(file, 'utf8')
  for (const { re, why } of BANNED) {
    for (const match of text.matchAll(re)) {
      const line = text.slice(0, match.index).split('\n').length
      problems.push(file + ':' + line + '  ' + match[0] + '  — ' + why)
    }
  }
  if (CHROME.includes(file)) {
    for (const match of text.matchAll(EMOJI)) {
      const line = text.slice(0, match.index).split('\n').length
      problems.push(
        file + ':' + line + '  ' + match[0]
          + '  — emoji ignore currentColor, so the active state cannot tint'
          + ' them; draw an icon in NavIcons.tsx instead',
      )
    }
  }
}

if (problems.length) {
  console.error('palette check failed (' + problems.length + '):')
  console.error('')
  for (const p of problems) console.error('  ' + p)
  console.error('')
  console.error('Colour belongs in tailwind.config.js, not in a class.')
  process.exit(1)
}
console.log('palette check passed (' + files.length + ' files)')
