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

// Filename a contribution package gets when the user isn't picking a path by
// hand. The incident id is always included: the backend refuses to overwrite an
// existing destination, so deriving the name from source_stem alone made every
// incident after the first from the same clip fail during a batch contribute.
export function contributionFileName(incidentId: string, sourceStem?: string): string {
  const clean = (value: string) => value.trim().replace(/[<>:"/\\|?*]+/g, '_')
  const id = clean(incidentId) || 'incident'
  const stem = clean(sourceStem || '')
  const base = stem ? `${stem}-${id}` : id
  return `${base}.mimir-dataset.age`
}
