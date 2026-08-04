import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

test('Mimir shell meets automated accessibility checks', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('mimir_onboarding_completed', 'true')
    window.localStorage.setItem('mimir_beta_privacy_notice_accepted', 'true')
  })
  const viewportResults: Array<{
    name: string
    width: number
    height: number
    seriousViolations: unknown[]
    horizontalOverflow: boolean
  }> = []
  for (const viewport of [
    { name: 'supported-minimum', width: 1080, height: 720 },
    { name: 'reference-desktop', width: 1280, height: 820 },
    { name: 'large-desktop', width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('body')).toBeVisible()
    const axe = await new AxeBuilder({ page }).analyze()
    const seriousViolations = axe.violations.filter(
      item => item.impact === 'critical' || item.impact === 'serious',
    )
    const horizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    )
    viewportResults.push({ ...viewport, seriousViolations, horizontalOverflow })
  }
  const serious = viewportResults.flatMap(item => item.seriousViolations)

  await page.setViewportSize({ width: 1280, height: 820 })
  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await page.keyboard.press('Tab')
  const focusVisible = await page.evaluate(() => {
    const active = document.activeElement
    if (!(active instanceof HTMLElement) || active === document.body) return false
    const style = window.getComputedStyle(active)
    return style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) >= 2
  })

  const keyboardTargets = new Set<string>()
  const keyboardFocusRecords: Array<{ key: string; visible: boolean }> = []
  let keyboardFocusVisible = focusVisible
  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press('Tab')
    const focused = await page.evaluate(() => {
      const active = document.activeElement
      if (!(active instanceof HTMLElement) || active === document.body) {
        return { key: '', visible: false }
      }
      const style = window.getComputedStyle(active)
      const hasOutline = style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) >= 2
      const hasRing = style.boxShadow !== 'none'
      return {
        key: `${active.tagName}:${active.getAttribute('aria-label') || active.textContent || ''}`,
        visible: hasOutline || hasRing,
      }
    })
    if (focused.key) keyboardTargets.add(focused.key)
    keyboardFocusRecords.push(focused)
    keyboardFocusVisible = keyboardFocusVisible && (!focused.key || focused.visible)
  }
  const keyboardTraversal = keyboardTargets.size >= 3 && keyboardFocusVisible

  await page.addStyleTag({ content: ':root { font-size: 125% !important; }' })
  const enlargedTextNoOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
  )

  const reducedMotionDetails = await page.evaluate(() => {
    const probe = document.createElement('div')
    probe.className = 'mimir-stage-orbit'
    document.body.appendChild(probe)
    const style = window.getComputedStyle(probe)
    const result = {
      active: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      duration_sec: Number.parseFloat(style.animationDuration),
      iteration_count: style.animationIterationCount,
    }
    probe.remove()
    return result
  })
  const reducedMotion = reducedMotionDetails.active && reducedMotionDetails.duration_sec <= 0.001

  await page.emulateMedia({ forcedColors: 'active' })
  const forcedColorsActive = await page.evaluate(() => window.matchMedia('(forced-colors: active)').matches)
  const screenReaderManualPassed = process.env.MIMIR_SCREEN_READER_MANUAL_PASSED === 'true'
  const scalingManualPassed = process.env.MIMIR_SCALING_MANUAL_PASSED === 'true'
  const manualPassed = screenReaderManualPassed && scalingManualPassed
  const automatedPassed =
    serious.length === 0 &&
    viewportResults.every(item => !item.horizontalOverflow) &&
    focusVisible &&
    keyboardTraversal &&
    enlargedTextNoOverflow &&
    reducedMotion &&
    forcedColorsActive
  const report = {
    schema_version: 'mimir_accessibility_report_v1',
    generated_at: new Date().toISOString(),
    passed: automatedPassed && manualPassed,
    automated_passed: automatedPassed,
    manual_desktop_passed: manualPassed,
    summary: automatedPassed
      ? manualPassed
        ? 'automated and manual desktop accessibility checks passed'
        : 'automated checks passed; screen-reader and desktop scaling evidence is still required'
      : 'one or more automated accessibility checks failed',
    checks: {
      serious_axe_violations: serious,
      viewport_results: viewportResults,
      focus_visible: focusVisible,
      keyboard_traversal: keyboardTraversal,
      keyboard_targets: [...keyboardTargets],
      keyboard_focus_records: keyboardFocusRecords,
      enlarged_text_no_horizontal_overflow: enlargedTextNoOverflow,
      reduced_motion: reducedMotion,
      reduced_motion_details: reducedMotionDetails,
      forced_colors: forcedColorsActive,
      screen_reader_manual: screenReaderManualPassed,
      scaling_manual: scalingManualPassed,
    },
  }
  const output = resolve('release_assets', 'accessibility_report.json')
  mkdirSync(resolve('release_assets'), { recursive: true })
  writeFileSync(output, JSON.stringify(report, null, 2) + '\n', 'utf8')

  expect(serious).toEqual([])
  expect(viewportResults.every(item => !item.horizontalOverflow)).toBe(true)
  expect(focusVisible).toBe(true)
  expect(keyboardTraversal).toBe(true)
  expect(enlargedTextNoOverflow).toBe(true)
  expect(reducedMotion).toBe(true)
  expect(forcedColorsActive).toBe(true)
})
