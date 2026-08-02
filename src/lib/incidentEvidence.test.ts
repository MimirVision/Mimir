import { describe, expect, it } from 'vitest'
import { aiPendingReason, experimentalAiUsed } from './incidentEvidence'

describe('experimentalAiUsed', () => {
  it('is false for a clip still queued for asynchronous AI enrichment', () => {
    // This mirrors empty_ai_review()'s placeholder in ai_reviewer.py: a
    // model name is filled in before the real review runs, but ai_reviewed
    // is false and ai_evidence is empty. A model name alone must not read
    // as a completed opinion.
    const incident = {
      ai_reviewed: false,
      ai_model: 'qwen2.5vl:7b',
      ai_evidence: {},
      ai_review_skipped_reason: 'AI second opinion is queued for asynchronous enrichment.',
    }

    expect(experimentalAiUsed(incident as never)).toBe(false)
  })

  it('is true once the AI has actually reviewed the clip', () => {
    const incident = {
      ai_reviewed: true,
      ai_model: 'qwen2.5vl:7b',
      ai_evidence: { recommended_severity: 'IGNORE', confidence: 0.85 },
    }

    expect(experimentalAiUsed(incident as never)).toBe(true)
  })

  it('is true when ai_evidence carries real content even without the reviewed flag set', () => {
    const incident = {
      ai_evidence: { recommended_severity: 'REVIEW', confidence: 0.6 },
    }

    expect(experimentalAiUsed(incident as never)).toBe(true)
  })
})

describe('aiPendingReason', () => {
  it('surfaces the skipped reason for a not-yet-reviewed clip', () => {
    const incident = {
      ai_reviewed: false,
      ai_model: 'qwen2.5vl:7b',
      ai_evidence: {},
      ai_review_skipped_reason: 'AI second opinion is queued for asynchronous enrichment.',
    }

    expect(aiPendingReason(incident as never)).toBe('AI second opinion is queued for asynchronous enrichment.')
  })

  it('is empty once a real review exists', () => {
    const incident = {
      ai_reviewed: true,
      ai_evidence: { recommended_severity: 'IGNORE', confidence: 0.9 },
      ai_review_skipped_reason: 'AI second opinion is queued for asynchronous enrichment.',
    }

    expect(aiPendingReason(incident as never)).toBe('')
  })
})
