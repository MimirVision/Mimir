// Turning failures into something a person can act on.
//
// Tauri rejects with a plain string, Rust returns `error.to_string()`, and
// Windows I/O errors arrive as text like "The system cannot find the file
// specified. (os error 2)". Showing that as the headline tells a beta tester
// nothing about what to do next, so the raw text moves into a disclosure and a
// sentence takes its place.

/** Raw technical text for the "technical details" disclosure. */
export function errorDetail(error: unknown): string {
  if (typeof error === 'string') {
    return error
  }

  if (error instanceof Error) {
    return error.message
  }

  if (
    typeof error === 'object' &&
    error !== null &&
    'message' in error &&
    typeof (error as { message?: unknown }).message === 'string'
  ) {
    return (error as { message: string }).message
  }

  return String(error)
}

// Matched against the lowercased detail. Windows messages are matched by both
// their `os error N` suffix and their English text, because the suffix is
// absent when the failure is reported by a backend sidecar rather than by Rust.
const KNOWN_CAUSES: ReadonlyArray<{ match: RegExp; message: string }> = [
  {
    match: /os error 2\b|cannot find the file|no such file or directory/,
    message:
      'That file could not be found. It may have been moved, renamed, or deleted since the scan ran.',
  },
  {
    match: /os error 3\b|cannot find the path/,
    message:
      'That folder could not be found. If it is on a USB drive, check the drive is still connected.',
  },
  {
    match: /os error 5\b|access is denied|permission denied/,
    message:
      'Windows would not allow access to that location. It may be read-only, or need permission you do not have.',
  },
  {
    match: /os error 32\b|used by another process|being used by another/,
    message:
      'That file is open in another program. Close it and try again.',
  },
  {
    match: /os error 33\b|lock violation/,
    message: 'Part of that file is locked by another program. Close it and try again.',
  },
  {
    match: /os error 112\b|not enough space|no space left|disk is full/,
    message: 'There is not enough free disk space to finish. Free up some space and try again.',
  },
  {
    match: /os error 19\b|media is write protected|write.protected/,
    message: 'That drive is write-protected, so nothing can be saved to it.',
  },
  {
    match: /os error 21\b|device is not ready/,
    message: 'That drive is not ready. If it is a USB drive, reconnect it and try again.',
  },
]

/**
 * A sentence someone who has never seen a stack trace can act on.
 *
 * Unrecognised causes fall back to `fallback` rather than the raw text: an
 * unfamiliar OS string as the headline is the exact thing this replaces.
 */
export function friendlyMessage(error: unknown, fallback: string): string {
  const detail = errorDetail(error).toLowerCase()
  return KNOWN_CAUSES.find(cause => cause.match.test(detail))?.message ?? fallback
}

export interface DescribedError {
  /** Headline shown to the user. */
  message: string
  /** Raw text, for the disclosure. Empty when it would only repeat `message`. */
  detail: string
}

export function describeError(error: unknown, fallback: string): DescribedError {
  const detail = errorDetail(error)
  const message = friendlyMessage(error, fallback)
  return { message, detail: detail === message ? '' : detail }
}

/**
 * Collapse per-item failures from a bulk action into one notice: friendly
 * reasons in the headline, raw text in the disclosure.
 */
export function describeFailures(
  failures: ReadonlyArray<{ label: string; error: unknown }>,
  headline: string,
  fallback: string,
): DescribedError {
  const described = failures.map(failure => ({
    label: failure.label,
    ...describeError(failure.error, fallback),
  }))

  return {
    message: [headline, ...described.map(item => `${item.label}: ${item.message}`)].join('\n'),
    detail: described.map(item => `${item.label}: ${item.detail || item.message}`).join('\n'),
  }
}
