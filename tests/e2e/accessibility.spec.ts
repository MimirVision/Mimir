import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

test('Mimir shell meets automated accessibility checks', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('mimir_onboarding_completed', 'true')
    window.localStorage.setItem('mimir_beta_privacy_notice_accepted', 'true')
  })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('body')).toBeVisible()

  const axe = await new AxeBuilder({ page }).analyze()
  const serious = axe.violations.filter(item => item.impact === 'critical' || item.impact === 'serious')

  await page.keyboard.press('Tab')
  const focusVisible = await page.evaluate(() => {
    const active = document.activeElement
    if (!(active instanceof HTMLElement) || active === document.body) return false
    const style = window.getComputedStyle(active)
    return style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) >= 2
  })

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
  const manualPassed = process.env.MIMIR_ACCESSIBILITY_MANUAL_PASSED === 'true'
  const automatedPassed = serious.length === 0 && focusVisible && reducedMotion && forcedColorsActive
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
      focus_visible: focusVisible,
      reduced_motion: reducedMotion,
      reduced_motion_details: reducedMotionDetails,
      forced_colors: forcedColorsActive,
      screen_reader_manual: manualPassed,
      scaling_manual: manualPassed,
    },
  }
  const output = resolve('release_assets', 'accessibility_report.json')
  mkdirSync(resolve('release_assets'), { recursive: true })
  writeFileSync(output, JSON.stringify(report, null, 2) + '\n', 'utf8')

  expect(serious).toEqual([])
  expect(focusVisible).toBe(true)
  expect(reducedMotion).toBe(true)
  expect(forcedColorsActive).toBe(true)
})
