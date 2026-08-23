# Mimir Privacy And Retention

Mimir is local-first. Scans, thumbnails, key moments, notes, manual status changes,
and diagnostics remain on the user's Windows computer unless the user explicitly
exports or uploads them.

No account, telemetry service, payment service, or activation service is required
for the free beta. Experimental AI uses a locally configured model and is
off by default.

Mimir keeps session history in its per-user application data directory so a prior
review can be reopened. Users control retention by removing those generated session
folders. Deleting generated sessions does not delete source footage. File actions
are separate, explicit commands with a transaction journal.

Training footage is never collected automatically. Dataset export requires an
explicit incident-by-incident selection and a recorded consent statement. Export
creates an `age`-encrypted package for a recipient key that only Mimir's developer
holds; sending it is a separate, explicit in-app action distinct from the export
itself, and nothing is sent until that action is taken.

Verdict corrections work differently, and this changed. Disagreeing with a
verdict sends that correction -- what Mimir decided, what the user decided, and
the evidence behind it. Agreeing with a verdict is recorded in the local session
and not sent. The first correction asks permission and nothing is sent unless it
is given; after that they send without further prompting, which is the point, since
a correction that needs a second deliberate action is one that mostly does not
get made. A correction carries no video. Footage is included only when the user
ticks "Send the clip too" on that incident, so footage remains a per-item choice
even though the correction around it is not. All of it is encrypted on-device
before it leaves. The intake private key is
never included in the app. Paths are reduced to filenames in the annotation record
where practical, but video content may itself contain identifying information.

Diagnostics should redact source paths where practical. Users should inspect any
export before sharing it because filenames, notes, images, or videos may still be
personal data.
