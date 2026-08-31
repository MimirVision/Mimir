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
export const CORRECTIONS_SENT_KEY = 'mimir_corrections_sent'

/**
 * Whether this verdict is worth sending.
 *
 * Only disagreements travel. Agreeing is still recorded locally -- the session
 * keeps `user_status` either way -- but sending a confirmation for every clip
 * someone skims past is mostly traffic, and a reviewer works through hundreds
 * in a sitting.
 *
 * The cost is real and worth stating: a set built only from disagreements has
 * no examples of Mimir being right, so it can measure how often Mimir is wrong
 * when someone corrects it, and not a false-positive rate. The locked
 * evaluation set is what answers that, and it is built deliberately in Forge
 * rather than sampled from whoever happened to press a key.
 */
export function shouldSendCorrection(incident: MimirIncident, status: SeverityGroup): boolean {
  // normalizeSeverity falls back to IGNORE for anything it cannot read, so an
  // incident with no severity would look like agreement the moment someone
  // pressed Ignore, and the correction would vanish. Only a severity actually
  // recorded on the incident can count as something to agree with.
  const recorded = String(incident.final_severity ?? incident.severity ?? '').toUpperCase()
  if (!['IMPORTANT', 'REVIEW', 'IGNORE'].includes(recorded)) {
    return true
  }

  return normalizeSeverity(recorded) !== status
}

/** The choice string for a correction, in the vocabulary the harvester parses. */
export function correctionChoiceFor(incident: MimirIncident, status: SeverityGroup): string {
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

/**
 * How many corrections this install has sent.
 *
 * Correcting a verdict is the most valuable thing a user does and the least
 * rewarded: it takes a keystroke, changes nothing they can see, and its whole
 * purpose happens somewhere they will never look. Across 53 sessions and 4,837
 * reviewed incidents on the development machine, zero corrections were ever
 * harvested -- not because people would not correct, but because doing so
 * returned nothing.
 *
 * A count is the smallest honest answer to "did that do anything".
 */
export function correctionsSent(read = (key: string) => localStorage.getItem(key)): number {
  try {
    const value = Number(read(CORRECTIONS_SENT_KEY))
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0
  } catch {
    return 0
  }
}

export function recordCorrectionSent(
  read = (key: string) => localStorage.getItem(key),
  write = (key: string, value: string) => localStorage.setItem(key, value),
): number {
  const next = correctionsSent(read) + 1
  try {
    write(CORRECTIONS_SENT_KEY, String(next))
  } catch {
    // A full or blocked store must never break the correction itself.
  }
  return next
}
