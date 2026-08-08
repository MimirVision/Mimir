import { describe, expect, it } from 'vitest'
import { activeStageIndex, formatStage, scanStages, stageStatus } from './ActiveScanStatus'

describe('ActiveScanStatus progress mapping', () => {
  it('maps every backend local-scan stage to the correct visible row', () => {
    const stages = [
      'reading_clips',
      'reading_event_metadata',
      'grouping_camera_angles',
      // Only emitted when the scan is importing footage, but it still owns a
      // row, so it has to sit here or every stage after it maps one row early.
      // It interleaves with detection rather than finishing before it -- each
      // event group is copied, then scanned, then cleared.
      'copying_footage',
      'detecting_activity',
      'reviewing_suspicious_moments',
      'building_incident_timeline',
      'writing_results',
    ]

    stages.forEach((stage, index) => {
      expect(activeStageIndex(stage)).toBe(index)
      expect(stageStatus(index, activeStageIndex(stage), 'running')).toBe('active')
      expect(
        scanStages.filter((_, rowIndex) => stageStatus(rowIndex, activeStageIndex(stage), 'running') === 'active'),
      ).toHaveLength(1)
    })
  })

  it('maps the deferred AI enrichment pass to Reviewing suspicious moments, not back to stage 0', () => {
    // Real bug: ai_enrichment.py emits stage="ai_enrichment" for the
    // deferred "enhanced AI second opinion" pass that runs after the main
    // scan completes. That key wasn't in this stage's list, so
    // activeStageIndex fell back to 0 ("Reading clips") -- making a later
    // phase look like the scan had restarted from the beginning.
    const reviewingIndex = scanStages.findIndex(stage => stage.label === 'Reviewing suspicious moments')
    expect(activeStageIndex('ai_enrichment')).toBe(reviewingIndex)
    expect(activeStageIndex('ai_enrichment')).not.toBe(0)
  })

  it('marks all stages complete once local results are ready', () => {
    const completedIndex = activeStageIndex('local_results_ready')
    expect(completedIndex).toBe(scanStages.length)
    expect(formatStage('local_results_ready')).toBe('Scan complete')
    expect(scanStages.every((_, index) => stageStatus(index, completedIndex, 'running') === 'complete')).toBe(true)
  })

  it('keeps pending stages still and advances completed stages', () => {
    const currentIndex = activeStageIndex('detecting_activity')
    expect(stageStatus(0, currentIndex, 'running')).toBe('complete')
    expect(stageStatus(currentIndex, currentIndex, 'running')).toBe('active')
    expect(stageStatus(currentIndex + 1, currentIndex, 'running')).toBe('idle')
  })
})
