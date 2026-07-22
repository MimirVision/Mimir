# Mimir Privacy And Retention

Mimir is local-first. Scans, thumbnails, key moments, notes, manual status changes,
and diagnostics remain on the user's Windows computer unless the user explicitly
exports or uploads them.

No account, telemetry service, payment service, or activation service is required
for the free private beta. Experimental AI uses a locally configured model and is
off by default.

Mimir keeps session history in its per-user application data directory so a prior
review can be reopened. Users control retention by removing those generated session
folders. Deleting generated sessions does not delete source footage. File actions
are separate, explicit commands with a transaction journal.

Training footage is never collected automatically. Dataset export requires an
explicit incident-by-incident selection and a recorded consent statement. Export
is local; upload is a separate human action. Paths are reduced to filenames in the
annotation record where practical, but video content may itself contain identifying
information.

Diagnostics should redact source paths where practical. Users should inspect any
export before sharing it because filenames, notes, images, or videos may still be
personal data.
