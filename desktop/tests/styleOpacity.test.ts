import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Tailwind silently drops an opacity modifier that is not on its scale.
 *
 * `bg-black/16` generates nothing at all -- 16 is not a step on Tailwind's
 * opacity scale, which goes in fives -- so the declaration disappears with no
 * warning at build time and the element falls back to whatever it inherits. The
 * library's search input fell back to the browser's default white background
 * and rendered cream text on it at a contrast ratio of 1.12, which is
 * unreadable. That had been dismissed as a quirk of the accessibility checker.
 *
 * 171 declarations across 14 files were being thrown away like this. Exact
 * arbitrary values (`bg-black/[0.16]`) always generate, so they are the fix and
 * this is the guard.
 */

const SCALE = new Set([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100])

const COLOUR_UTILITIES = [
  'bg', 'text', 'border', 'from', 'to', 'via', 'ring', 'shadow', 'divide',
  'placeholder', 'outline', 'decoration', 'accent', 'fill', 'stroke',
]

// A bare numeric opacity modifier: the `/16` in `bg-black/16`. Bracketed values
// and decimals are excluded, since those always generate.
const MODIFIER = /([\w-]+)\/(\d{1,3})(?![\d.\]])/g

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap(entry => {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) {
      return sourceFiles(path)
    }
    return /\.tsx?$/.test(entry) && !entry.endsWith('.test.ts') ? [path] : []
  })
}

describe('Tailwind opacity modifiers', () => {
  it('are all on a scale step, so none is silently discarded', () => {
    const dropped: string[] = []

    for (const path of sourceFiles(join(process.cwd(), "src"))) {
      const lines = readFileSync(path, 'utf8').split('\n')
      lines.forEach((line, index) => {
        for (const match of line.matchAll(MODIFIER)) {
          const [token, base, digits] = match
          if (!COLOUR_UTILITIES.includes(base.split('-')[0])) {
            continue
          }
          if (SCALE.has(Number(digits))) {
            continue
          }
          const exact = `${base}/[${Number(digits) / 100}]`
          dropped.push(`${path}:${index + 1}  ${token}  ->  write it as ${exact}`)
        }
      })
    }

    expect(dropped).toEqual([])
  })
})
