// Rendering only the rows of a grid that are on screen.
//
// The library renders every incident at once. On a real week that is 656
// cards, each with a thumbnail, and the grid scrolls poorly long before the
// count gets interesting. It is also the reason any cross-session view is
// blocked: aggregating several scans multiplies a number that is already too
// large.
//
// No windowing library. react-window and friends want a fixed column count,
// and the grid is `repeat(auto-fill, minmax(238px, 1fr))` -- the number of
// columns is whatever fits. Deriving that from the measured width is a dozen
// lines, and it avoids a dependency that the release audit would then have to
// carry forever.
//
// The arithmetic lives here, apart from React, because it is the part that can
// be wrong in ways that are invisible until someone scrolls to the bottom of a
// long list and finds nothing there.

export interface GridWindowInput {
  /** Width available to the grid, in pixels. */
  containerWidth: number
  /** Narrowest a column may be before the grid drops one. Matches minmax(). */
  minColumnWidth: number
  /** Gap between columns and rows. */
  gap: number
  /** Measured height of one card, including its gap. */
  rowHeight: number
  /** How far the grid's top edge is above the viewport top. Negative when scrolled past. */
  offsetTop: number
  viewportHeight: number
  itemCount: number
  /** Extra rows kept mounted above and below, so scrolling does not flash. */
  overscanRows?: number
}

export interface GridWindow {
  columns: number
  totalRows: number
  firstIndex: number
  /** Exclusive. */
  lastIndex: number
  paddingTop: number
  paddingBottom: number
}

/**
 * How many columns `repeat(auto-fill, minmax(min, 1fr))` will produce.
 *
 * n columns need n*min + (n-1)*gap pixels. Solving for n and flooring gives
 * the count the browser settles on. Always at least one, because a container
 * narrower than a single column still shows that column rather than nothing.
 */
export function columnsForWidth(containerWidth: number, minColumnWidth: number, gap: number): number {
  if (containerWidth <= 0 || minColumnWidth <= 0) return 1
  return Math.max(1, Math.floor((containerWidth + gap) / (minColumnWidth + gap)))
}

/**
 * Which slice of items to mount, and how much empty space to leave either side.
 *
 * Returns the whole list when the inputs are not yet measured. A grid that
 * renders nothing because a ResizeObserver has not fired is worse than one
 * that briefly renders everything, and this runs before first paint.
 */
export function computeGridWindow(input: GridWindowInput): GridWindow {
  const {
    containerWidth,
    minColumnWidth,
    gap,
    rowHeight,
    offsetTop,
    viewportHeight,
    itemCount,
    overscanRows = 2,
  } = input

  const columns = columnsForWidth(containerWidth, minColumnWidth, gap)
  const totalRows = Math.ceil(itemCount / columns)

  if (rowHeight <= 0 || viewportHeight <= 0 || itemCount === 0) {
    return {
      columns,
      totalRows,
      firstIndex: 0,
      lastIndex: itemCount,
      paddingTop: 0,
      paddingBottom: 0,
    }
  }

  // How far the first row has scrolled above the viewport. offsetTop is
  // negative once the grid's top passes the top of the screen.
  const scrolledPast = Math.max(0, -offsetTop)
  const firstVisibleRow = Math.floor(scrolledPast / rowHeight)
  const rowsOnScreen = Math.ceil(viewportHeight / rowHeight)

  const firstRow = Math.max(0, firstVisibleRow - overscanRows)
  const lastRow = Math.min(totalRows, firstVisibleRow + rowsOnScreen + overscanRows)

  return {
    columns,
    totalRows,
    firstIndex: firstRow * columns,
    lastIndex: Math.min(itemCount, lastRow * columns),
    paddingTop: firstRow * rowHeight,
    // Rows below the window still have to occupy space, or the scrollbar
    // shrinks as you scroll and the page fights you.
    paddingBottom: Math.max(0, (totalRows - lastRow) * rowHeight),
  }
}
