#!/usr/bin/env node
/**
 * Fail the build on a date-only string parsed as UTC.
 *
 * `new Date('2026-11-23')` is midnight UTC. Anywhere west of Greenwich that
 * renders as the 22nd, so a calendar day arrives labelled a day early. This
 * app has now shipped that bug three times: the diet picker in November, the
 * health summary list and staleness line, and the progress chart's x-axis --
 * each one harmless-looking in its own diff, each one found by a person
 * staring at the wrong date on their phone.
 *
 * The rule is mechanical: a value whose NAME says it is a date does not go
 * through the `Date` constructor. It goes through `parseLocal`, which reads
 * a date-only string as the local day and leaves a full timestamp alone --
 * so it is also the right call when you are not sure which shape you have.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = 'src'

// The helper itself is where the Date constructor is allowed to live, and a
// test may construct whatever it is asserting about.
const ALLOWED = (file) =>
  file.endsWith('lib/localDate.ts') || /\.test\.tsx?$/.test(file)

// Catches a value whose NAME says "date" going straight into the
// constructor. It cannot catch everything -- the worst instance of this bug
// reached production as `new Date(iso)` inside a helper, where nothing in
// the expression says what shape `iso` is -- so the unit tests in
// lib/localDate.test.ts are the real guard and this is the cheap net over
// the call sites. Expressions containing a quote are skipped: those are the
// existing `+ 'T00:00:00'` forms, which are already correct.
const ARG = "[^)'\"`]*[Dd]ate[^)'\"`]*"
const BANNED = [
  {
    re: new RegExp('new Date\\(\\s*' + ARG + '\\)\\s*\\.toLocale', 'g'),
    why: 'formatting a date-named value through the Date constructor;'
      + ' use formatDayLabel() from lib/localDate',
  },
  {
    re: new RegExp('new Date\\(\\s*' + ARG + '\\)\\s*\\.getTime\\(\\)', 'g'),
    why: 'date arithmetic on a date-named value; use daysSince() or'
      + ' parseLocal() from lib/localDate',
  },
]

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}

const files = walk(ROOT).filter((f) => /\.tsx?$/.test(f) && !ALLOWED(f))
const problems = []
for (const file of files) {
  const text = readFileSync(file, 'utf8')
  for (const { re, why } of BANNED) {
    for (const match of text.matchAll(re)) {
      const line = text.slice(0, match.index).split('\n').length
      problems.push(file + ':' + line + '  ' + match[0].trim() + '  — ' + why)
    }
  }
}

if (problems.length) {
  console.error('date check failed (' + problems.length + '):')
  console.error('')
  for (const p of problems) console.error('  ' + p)
  console.error('')
  console.error('A calendar day is not an instant. parseLocal() handles both.')
  process.exit(1)
}
console.log('date check passed (' + files.length + ' files)')
