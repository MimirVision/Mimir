import type { MimirIncident } from '../types'
import { normalizeSeverity, type SeverityGroup } from './incidentStatus'

/**
 * Correcting a verdict is the feedback.
 *
 * There used to be two ways to tell Mimir it was wrong: change the status
 * (one keystroke, written to the local session and going nowhere) or open the
 * feedback panel and say the same thing again in different words ("Should be
 * Ignore" rather than "Ignore"). The cheap action was inert and the expensive
 * one was the only one that shipped, which is why 4,837 reviewed incidents
 * produced zero harvested corrections and 33 feedback packages.
 *
 * So the correction now sends itself. The payload keeps the wording the
 * pipeline already understands -- harvest_feedback_labels.py derives an
 * expected severity from these exact strings -- so nothing downstream has to
 * change for the UI to collapse into one action.
 */

export const CORRECTION_CONSENT_KEY = 'mimir_send_corrections'

/** The choice string for a correction, in the vocabulary the harvester parses. */
export function correctionChoiceFor(incident: MimirIncident, status: SeverityGroup): string {
  // Agreeing is worth sending too: a set of only disagreements measures
  // nothing, because it has no examples of Mimir being right.
  if (normalizeSeverity(incident.final_severity ?? incident.severity) === status) {
    return 'Correct'
  }

  return {
    IMPORTANT: 'Should be Important',
    REVIEW: 'Should be Review',
    IGNORE: 'Should be Ignore',
  }[status]
}

export type CorrectionConsent = 'granted' | 'declined' | 'unasked'

export function correctionConsent(read = (key: string) => localStorage.getItem(key)): CorrectionConsent {
  try {
    const value = read(CORRECTION_CONSENT_KEY)
    return value === 'granted' || value === 'declined' ? value : 'unasked'
  } catch {
    // A locked-down webview with no storage must not stop someone reviewing
    // clips; it just means corrections are never sent.
    return 'declined'
  }
}

export function setCorrectionConsent(
  value: Exclude<CorrectionConsent, 'unasked'>,
  write = (key: string, item: string) => localStorage.setItem(key, item),
) {
  try {
    write(CORRECTION_CONSENT_KEY, value)
  } catch {
    // Nothing to do. The next call reads 'declined' and stays quiet.
  }
}

/** Whether this correction should be sent now, asked about, or dropped. */
export function correctionOutcome(consent: CorrectionConsent): 'send' | 'ask' | 'skip' {
  if (consent === 'granted') return 'send'
  if (consent === 'unasked') return 'ask'
  return 'skip'
}
