// Contributor consent details, remembered locally after the first time.
//
// Consent itself stays explicit -- the user still confirms rights before
// anything is packaged, and nothing ever uploads on its own. What this
// removes is re-typing the *same* answers ("this is my own footage, my
// name is X") for every single incident, which was the real friction.
// The saved values are only ever read back into a confirmation the user
// still has to accept.

export type RightsBasis = 'owned' | 'explicit_permission' | 'public_license'

export interface ContributorIdentity {
  recordedBy: string
  rightsBasis: RightsBasis
  permissionReference: string
}

const STORAGE_KEY = 'mimir_contributor_identity_v1'

const VALID_BASES: RightsBasis[] = ['owned', 'explicit_permission', 'public_license']

export function readContributorIdentity(): ContributorIdentity | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as Partial<ContributorIdentity>
    const recordedBy = String(parsed.recordedBy ?? '').trim()
    const permissionReference = String(parsed.permissionReference ?? '').trim()
    const rightsBasis = parsed.rightsBasis as RightsBasis

    if (!recordedBy || !permissionReference || !VALID_BASES.includes(rightsBasis)) {
      return null
    }

    return { recordedBy, rightsBasis, permissionReference }
  } catch {
    return null
  }
}

export function saveContributorIdentity(identity: ContributorIdentity): boolean {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        recordedBy: identity.recordedBy.trim(),
        rightsBasis: identity.rightsBasis,
        permissionReference: identity.permissionReference.trim(),
      }),
    )
    return true
  } catch {
    return false
  }
}


export function rightsBasisLabel(basis: RightsBasis): string {
  if (basis === 'owned') {
    return 'I own this footage'
  }

  if (basis === 'explicit_permission') {
    return 'I have explicit permission'
  }

  return 'Public license'
}

// The consent receipt requires a non-empty permission reference -- enforced in
// three places (this form, main.rs's valid_free_text_argument, and
// dataset_package.py's consent validation), because it is what makes the
// receipt auditable rather than a bare checkbox.
//
// It was one field labelled "Ownership, permission, or license reference" for
// all three bases. Someone contributing footage from their own car has no idea
// what to type there, and it is required, so the common case dead-ends. These
// make the question answerable per basis without weakening what gets recorded:
// the user still types their own attestation, it is just a question they can
// actually answer.

export function permissionReferenceLabel(basis: RightsBasis): string {
  if (basis === 'owned') {
    return 'How do you know this footage is yours?'
  }

  if (basis === 'explicit_permission') {
    return 'Who gave permission, and how?'
  }

  return 'Which license permits this use?'
}

export function permissionReferencePlaceholder(basis: RightsBasis): string {
  if (basis === 'owned') {
    return 'e.g. Recorded by my own Tesla parked outside my house'
  }

  if (basis === 'explicit_permission') {
    return 'e.g. Written permission from the vehicle owner, 2026-07-14'
  }

  return 'e.g. CC BY 4.0, with a link to the source'
}

export function permissionReferenceHelp(basis: RightsBasis): string {
  if (basis === 'owned') {
    return 'A short sentence in your own words is enough. It is stored with the clip so the consent record says how you came to have it.'
  }

  if (basis === 'explicit_permission') {
    return 'Point at something checkable later -- who granted it, when, and in what form. Attach the record itself below if you have it.'
  }

  return 'Name the license and where the footage came from, so the terms can be checked later.'
}
