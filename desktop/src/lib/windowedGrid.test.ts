import { describe, expect, it } from 'vitest'
import { columnsForWidth, computeGridWindow } from './windowedGrid'

// The library grid: repeat(auto-fill, minmax(238px, 1fr)) with gap-4.
const MIN = 238
const GAP = 16
const ROW = 220

function windowFor(overrides: Partial<Parameters<typeof computeGridWindow>[0]> = {}) {
  return computeGridWindow({
    containerWidth: 1200,
    minColumnWidth: MIN,
    gap: GAP,
    rowHeight: ROW,
    offsetTop: 0,
    viewportHeight: 800,
    itemCount: 656,
    ...overrides,
  })
}

describe('columnsForWidth', () => {
  it('matches what auto-fill actually produces', () => {
    // n columns need n*238 + (n-1)*16.
    expect(columnsForWidth(238, MIN, GAP)).toBe(1)
    // Two columns need 238*2 + 16 = 492. One pixel short is still one column.
    expect(columnsForWidth(491, MIN, GAP)).toBe(1)
    expect(columnsForWidth(492, MIN, GAP)).toBe(2)
    expect(columnsForWidth(746, MIN, GAP)).toBe(3) // 238*3 + 16*2
    expect(columnsForWidth(1200, MIN, GAP)).toBe(4)
  })

  it('never returns zero, however narrow the container', () => {
    // A container narrower than one column still shows that column. Returning
    // 0 would divide by zero in the row maths and mount nothing at all.
    expect(columnsForWidth(100, MIN, GAP)).toBe(1)
    expect(columnsForWidth(0, MIN, GAP)).toBe(1)
    expect(columnsForWidth(-50, MIN, GAP)).toBe(1)
  })
})

describe('computeGridWindow', () => {
  it('mounts a screenful plus overscan, not 656 cards', () => {
    const result = windowFor()

    expect(result.columns).toBe(4)
    expect(result.totalRows).toBe(164)
    // 800px of viewport over 220px rows is 4 rows, plus 2 rows of overscan.
    expect(result.lastIndex - result.firstIndex).toBeLessThan(40)
    expect(result.firstIndex).toBe(0)
  })

  it('keeps the scroll height constant as the window moves', () => {
    // Otherwise the scrollbar shrinks while you scroll and the page fights you.
    const top = windowFor({ offsetTop: 0 })
    const middle = windowFor({ offsetTop: -8000 })
    const height = (w: typeof top) =>
      w.paddingTop + w.paddingBottom + Math.ceil((w.lastIndex - w.firstIndex) / w.columns) * ROW

    expect(height(top)).toBe(164 * ROW)
    expect(height(middle)).toBe(164 * ROW)
  })

  it('moves the window as the grid scrolls out of view', () => {
    const scrolled = windowFor({ offsetTop: -8000 })

    // 8000 / 220 = row 36, minus 2 overscan rows, times 4 columns.
    expect(scrolled.firstIndex).toBe(34 * 4)
    expect(scrolled.paddingTop).toBe(34 * ROW)
  })

  it('reaches the final item when scrolled to the bottom', () => {
    // The bug this guards: an off-by-one in the last row leaves the tail of a
    // long list permanently unreachable, and it is invisible until someone
    // scrolls all the way down.
    const bottom = windowFor({ offsetTop: -(164 * ROW - 800) })

    expect(bottom.lastIndex).toBe(656)
    expect(bottom.paddingBottom).toBe(0)
  })

  it('renders everything when nothing has been measured yet', () => {
    // A ResizeObserver has not fired on first paint. Rendering nothing at that
    // moment is worse than briefly rendering all of it.
    const unmeasured = windowFor({ rowHeight: 0 })

    expect(unmeasured.firstIndex).toBe(0)
    expect(unmeasured.lastIndex).toBe(656)
  })

  it('handles an empty list without producing a negative window', () => {
    const empty = windowFor({ itemCount: 0 })

    expect(empty.totalRows).toBe(0)
    expect(empty.firstIndex).toBe(0)
    expect(empty.lastIndex).toBe(0)
    expect(empty.paddingBottom).toBe(0)
  })

  it('never asks for more items than exist', () => {
    const few = windowFor({ itemCount: 3 })

    expect(few.lastIndex).toBe(3)
    expect(few.paddingBottom).toBe(0)
  })

  it('re-columns when the window narrows', () => {
    const wide = windowFor({ containerWidth: 1200 })
    const narrow = windowFor({ containerWidth: 500 })

    expect(wide.columns).toBe(4)
    expect(narrow.columns).toBe(2)
    expect(narrow.totalRows).toBe(328)
  })
})
