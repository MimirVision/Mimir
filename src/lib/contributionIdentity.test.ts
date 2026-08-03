import { describe, expect, it } from 'vitest'
import {
  permissionReferenceHelp,
  permissionReferenceLabel,
  permissionReferencePlaceholder,
  rightsBasisLabel,
  type RightsBasis,
} from './contributionIdentity'

// The permission reference is required in three separate places (this form,
// main.rs's valid_free_text_argument, dataset_package.py's consent check), so
// an unanswerable prompt here is a hard dead end -- which is the most likely
// reason zero real contributions arrived during the whole free beta.

const BASES: RightsBasis[] = ['owned', 'explicit_permission', 'public_license']

describe('rightsBasisLabel', () => {
  it('names every basis distinctly', () => {
    const labels = BASES.map(rightsBasisLabel)
    expect(new Set(labels).size).toBe(BASES.length)
    expect(labels.every(label => label.length > 0)).toBe(true)
  })
})

describe('permission reference prompts', () => {
  it('asks a different, answerable question per basis', () => {
    const labels = BASES.map(permissionReferenceLabel)
    expect(new Set(labels).size).toBe(BASES.length)
    // Every prompt is a question. The old single label was a noun phrase
    // ("Ownership, permission, or license reference"), which is exactly what
    // made it unanswerable for someone contributing their own footage.
    expect(labels.every(label => label.endsWith('?'))).toBe(true)
  })

  it('gives the owned case a concrete example rather than legalese', () => {
    expect(permissionReferenceLabel('owned')).toBe('How do you know this footage is yours?')
    expect(permissionReferencePlaceholder('owned')).toMatch(/^e\.g\./)
    expect(permissionReferenceHelp('owned')).toMatch(/own words/i)
  })

  it('offers a worked example for every basis', () => {
    for (const basis of BASES) {
      expect(permissionReferencePlaceholder(basis)).toMatch(/^e\.g\. .+/)
    }
  })

  it('explains why the answer is kept, for every basis', () => {
    for (const basis of BASES) {
      expect(permissionReferenceHelp(basis).length).toBeGreaterThan(40)
    }
  })

  it('never returns an empty prompt, which would recreate the dead end', () => {
    for (const basis of BASES) {
      expect(permissionReferenceLabel(basis).trim()).not.toBe('')
      expect(permissionReferencePlaceholder(basis).trim()).not.toBe('')
      expect(permissionReferenceHelp(basis).trim()).not.toBe('')
    }
  })
})
