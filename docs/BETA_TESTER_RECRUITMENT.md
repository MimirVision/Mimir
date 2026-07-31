# Testing Mimir

A short, honest pitch for anyone considering joining the beta — meant to be
shared as-is with a Tesla owner you think might want in.

## What Mimir is

Mimir is a Windows desktop app that scans Tesla Sentry footage on your own PC and
sorts it into Important / Review / Ignore, so you don't have to scrub through hours
of clips after every alert. It runs entirely on your machine: no account, no cloud
upload, no subscription. You plug in the USB drive (or point it at a folder of
clips), it scans locally, and you review what it found.

## What being a tester actually involves

- Install it, run it against your real Sentry footage, use it the way you'd
  normally check your Sentry clips.
- Tell us when it gets something wrong — missed a real event, flagged something
  harmless as important, mislabeled severity. Specific examples (which clip, what
  you expected) are far more useful than "detection feels off."
- Rear-end contact and door-ding/close-contact events are the current priority —
  those are the cases we most want to know about, in both directions (missed and
  false alarm).
- No fixed time commitment. Use it when you'd normally review Sentry footage
  anyway; there's nothing to schedule or show up for.

## What you get

- Free access to the app for as long as the beta runs — no payment, no
  trial expiry.
- A tool that's actually useful today (local review, manual library/trash
  management, no forced cloud), not a placeholder for a future product.
- Direct influence: this is a small beta group, so specific feedback has a real
  chance of showing up in the next build rather than a backlog.

## What to expect going in (the honest part)

- The detector shipping today is a stock, off-the-shelf object detector. It has
  **not** been fine-tuned on real Sentry footage yet — no consented training data
  has been collected and used for that. It works, but "kinda perfect" it is not.
  See [MODEL_CARD.md](MODEL_CARD.md) for exactly what it is and isn't.
- It can miss real events and can flag ordinary activity as noteworthy. You are
  still the one deciding what actually happened — see
  [LIMITATIONS.md](LIMITATIONS.md) for the specifics.
- The public beta installer is signed. Do not install internal or unsigned builds.
- There's no cloud, no account, and no background upload of your footage. If you
  choose to contribute a clip toward improving the detector, that's a separate,
  explicit, opt-in action — see [DATA_CONTRIBUTION.md](DATA_CONTRIBUTION.md) and
  [PRIVACY.md](PRIVACY.md) for exactly what that does and doesn't involve.
- Full terms: [FREE_BETA_TERMS.md](FREE_BETA_TERMS.md).

## How to join

The installer is a free public download -- no invite, no account, no signup.
Grab it from the download page, which also carries the tester guide
([PUBLIC_BETA_TESTING.md](../release_assets/PUBLIC_BETA_TESTING.md)) covering install steps
and where to send feedback.

Note that the strict release gate (`mimir_core_v2_release_check.py`) must pass
before the public beta installer is published. If it is red, the build stays
internal until the missing evidence is complete.
