import { describe, expect, it } from 'vitest'
import {
  correctionChoiceFor,
  shouldSendCorrection,
  correctionConsent,
  correctionOutcome,
  setCorrectionConsent,
} from './verdictCorrection'
import type { MimirIncident } from '../types'

const incident = (severity: string) => ({ final_severity: severity } as unknown as MimirIncident)

describe('correctionChoiceFor', () => {
  // These exact strings are a contract with harvest_feedback_labels.py, which
  // derives an expected severity from them. A typo here does not fail loudly --
  // it produces a label the harvester files as "needs_human", silently costing
  // the evaluation set a row.
  it('speaks the vocabulary the harvester parses', () => {
    expect(correctionChoiceFor(incident('IMPORTANT'), 'IGNORE')).toBe('Should be Ignore')
    expect(correctionChoiceFor(incident('IGNORE'), 'REVIEW')).toBe('Should be Review')
    expect(correctionChoiceFor(incident('REVIEW'), 'IMPORTANT')).toBe('Should be Important')
  })

  it('reports agreement when the user picks what Mimir picked', () => {
    expect(correctionChoiceFor(incident('REVIEW'), 'REVIEW')).toBe('Correct')
    expect(correctionChoiceFor(incident('IGNORE'), 'IGNORE')).toBe('Correct')
  })
})

describe('shouldSendCorrection', () => {
  it('sends a disagreement', () => {
    expect(shouldSendCorrection(incident('IMPORTANT'), 'IGNORE')).toBe(true)
    expect(shouldSendCorrection(incident('IGNORE'), 'REVIEW')).toBe(true)
  })

  it('keeps agreement on the machine', () => {
    // Still written to the session as user_status; just not uploaded. A
    // confirmation for every clip someone skims past is mostly traffic.
    expect(shouldSendCorrection(incident('REVIEW'), 'REVIEW')).toBe(false)
    expect(shouldSendCorrection(incident('IMPORTANT'), 'IMPORTANT')).toBe(false)
  })

  it('treats an unreadable severity as a disagreement rather than dropping it', () => {
    // Losing a real correction is worse than one redundant upload.
    expect(shouldSendCorrection({} as never, 'IGNORE')).toBe(true)
  })

  it('treats a missing severity as something to correct, not agree with', () => {
    expect(correctionChoiceFor({} as MimirIncident, 'IMPORTANT')).toBe('Should be Important')
  })
})

describe('correctionConsent', () => {
  it('is unasked before anyone has been asked', () => {
    expect(correctionConsent(() => null)).toBe('unasked')
  })

  it('reads back what was granted or declined', () => {
    expect(correctionConsent(() => 'granted')).toBe('granted')
    expect(correctionConsent(() => 'declined')).toBe('declined')
  })

  it('treats an unrecognised value as never asked, rather than as consent', () => {
    expect(correctionConsent(() => 'yes please')).toBe('unasked')
  })

  it('declines when storage cannot be read at all', () => {
    // A webview with storage blocked must not send on a default of "granted".
    expect(correctionConsent(() => { throw new Error('blocked') })).toBe('declined')
  })

  it('does not throw when storage cannot be written', () => {
    expect(() => setCorrectionConsent('granted', () => { throw new Error('blocked') })).not.toThrow()
  })
})

describe('correctionOutcome', () => {
  it('sends only once consent has actually been given', () => {
    expect(correctionOutcome('granted')).toBe('send')
    expect(correctionOutcome('unasked')).toBe('ask')
    expect(correctionOutcome('declined')).toBe('skip')
  })
})

describe('withdrawing consent', () => {
  // The terms, the privacy notice and the README all say consent can be taken
  // back. Until the control existed that was false, so this pins the round trip.
  it('turns sending off again once it was on', () => {
    const store: Record<string, string> = {}
    const read = (key: string) => store[key] ?? null
    const write = (key: string, value: string) => {
      store[key] = value
    }

    setCorrectionConsent('granted', write)
    expect(correctionOutcome(correctionConsent(read))).toBe('send')

    setCorrectionConsent('declined', write)
    expect(correctionOutcome(correctionConsent(read))).toBe('skip')

    setCorrectionConsent('granted', write)
    expect(correctionOutcome(correctionConsent(read))).toBe('send')
  })
})
