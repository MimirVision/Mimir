// Turns raw age/intake error text into something scannable at a glance.
// Built directly from a real production sync: three items failed with
// "failed to parse header" (stray non-age test uploads, not real data) and
// one failed with "no identity matched" (a real submission encrypted under
// a since-lost key) -- two very different situations that deserve two very
// different messages, not one generic "failed to decrypt."

interface ExplainedFailure {
  headline: string
  benign: boolean
}

const KNOWN_CAUSES: ReadonlyArray<{ match: RegExp; explain: ExplainedFailure }> = [
  {
    match: /failed to parse header|failed to read line: EOF/,
    explain: {
      headline: "Not a valid submission -- likely a stray test upload, not real data. Safe to ignore.",
      benign: true,
    },
  },
  {
    match: /no identity matched any of the recipients/,
    explain: {
      headline: "Encrypted under a different key than the one currently loaded -- undecryptable, likely from before a key rotation.",
      benign: false,
    },
  },
  {
    match: /no consented dataset collections were found/,
    explain: {
      headline: "No valid consented collections in the dataset yet.",
      benign: true,
    },
  },
]

export function explainSyncFailure(rawError: string): ExplainedFailure {
  const match = KNOWN_CAUSES.find(cause => cause.match.test(rawError))
  return match?.explain ?? { headline: "Failed for an unrecognized reason -- see the raw error below.", benign: false }
}
