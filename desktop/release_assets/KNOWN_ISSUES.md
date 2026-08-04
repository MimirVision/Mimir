# Mimir free beta -- what to expect

Written so you can decide whether Mimir is worth your time before you spend
any. Everything here is measured or observed, not estimated. If something
below turns out to be wrong, that itself is worth reporting.

`PUBLIC_BETA_TESTING.md` covers how to send feedback; this file is what is
currently wrong.

## The big one: Mimir over-flags

**Expect it to call things Important that are not.**

Of the feedback received during the beta so far, **18 of 19 clips that Mimir
rated IMPORTANT were rated lower by the person who looked at them.** That
number is not a measured false-positive rate -- people send feedback when
something is wrong, not when it is right, so the sample is skewed by
construction. But the direction is unambiguous and it matches what we see in
the detector's own development.

Practically: treat IMPORTANT as "look at this", not as "something happened".
Mimir is trying to save you from scrubbing hours of footage, not to tell you
what occurred.

**The single most useful thing you can do is tell us when it is wrong.** The
detector cannot improve without examples, and a clip Mimir got wrong is worth
more than one it got right.

## Speed

Measured on 255 real Sentry clips, mid-range desktop with a DirectX 12 GPU:

| | Per clip | 679-clip dump |
|---|---|---|
| Full scan | ~2.3 s | **~25 minutes** |
| Without object detection | ~1.0 s | ~11 minutes |

Object detection is 55-60% of that time. **If your machine has no DirectX 12
GPU, detection falls back to CPU and runs roughly 10x slower** -- a large dump
can take hours rather than minutes. The system check on the import screen tells
you which one you are getting.

Reading footage off a USB stick is often the real bottleneck. Copying to your
internal drive first is usually faster overall.

## Windows will warn you on first run

The installer is **not code-signed yet**, so SmartScreen shows a blue
"Windows protected your PC" dialog. You have to click *More info* then *Run
anyway*. That warning is accurate -- it means the publisher is unverified, not
that the file is safe. Certificates cost money and this is a free beta; it is
on the list.

## Other things we already know about

- **Large libraries get sluggish.** Every incident is rendered at once, so a
  session with several hundred incidents scrolls poorly. Filtering helps.
- **Some clips will not preview** even when the scan output is fine. The
  incident data is still correct; only playback fails.
- **Move to Mimir Trash is recoverable, but there is no in-app restore button.**
  The files are in a Mimir Trash folder and you can move them back by hand.
- **Windows 10/11, 64-bit only.** No macOS, no Linux, no 32-bit or ARM build.
  Requires WebView2, which is present on almost all current Windows installs.
- **Optional local AI is off by default** and is a second opinion only. It can
  never override hard local evidence, and a scan never waits on it.
- **No accounts, no cloud sync, no payments** in this beta.

## Things that are deliberately not automatic

Not bugs -- design decisions, listed because they surprise people:

- **Nothing is uploaded unless you press a button.** Scanning is entirely local.
  Feedback and footage contributions are separate, explicit, per-item actions.
- **Scanning never moves or deletes your clips.** File actions are separate
  commands you invoke yourself.
- **Contributions ask for consent details the first time.** Footage of other
  people carries obligations that feedback text does not, so the first
  contribution walks through who you are and on what basis you hold the
  footage. It is remembered afterwards.

## What Mimir will not do

- Prove that physical contact occurred. Overlapping shapes in an image are not
  evidence of touching, and Mimir says so rather than pretending otherwise.
- Identify people or read number plates.
- Decide anything for you. Every status is editable and your correction is
  kept separate from Mimir's original answer.
