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
