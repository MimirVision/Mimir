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

**What changed on 2026-08-05.** Two rules were tightened after reading that
feedback back. Both come down to the same mistake: motion near the car was
being treated as evidence that something touched it.

- Close activity seen by a **single camera**, with nothing directly observed to
  back it up, no longer forces IMPORTANT. One camera cannot tell a neighbour
  squeezing past from actual contact.
- **Proximity plus strong motion** no longer forces IMPORTANT on its own. That
  signal was set on every IMPORTANT in the feedback -- the 18 rated down *and*
  the 1 agreed with -- so by itself it distinguished nothing.

Replayed against that feedback, this removes 17 of the 18 IMPORTANTs a human
rejected while keeping the one they agreed with. Both cases drop to REVIEW, not
IGNORE: the clip is still put in front of you, just not at the top.

Take the improvement with the same salt as the original number. Nineteen clips,
selected by people who wrote in because something looked wrong, is a hint --
not a measured accuracy claim, and not a substitute for the evaluation set the
model card requires and that does not exist yet.

**And it moved the problem rather than solving it.** Measured on 2026-08-08
across every scan on the development machine (4,173 incidents), what you
actually get on a full week of footage is roughly:

| | out of 656 events |
|---|---|
| Ignored for you | 378 |
| **Put in front of you** | **278** |
| Marked Important | 0 |

So Mimir rules out a bit over half, and asks you to look at the rest. Better
than watching all 656. Nowhere near "ten instead of seven hundred", and Important
has become a tier that essentially never fires.

Almost every one of those 278 is flagged because something was near the car and
something moved. On a parked car, that is the permanent condition.

An earlier version of this page said the "nearby vehicle" setting it off was the
car's own bodywork or whatever is parked alongside. **That was measured on
2026-08-17 and it is not the main cause.** Only about 12% of those detections
sit where the car's own body is. What the measurement did find is a different
fault: on low-contrast night footage the detector sometimes returns a box around
the *entire frame*, sky and ground included, and calls it a vehicle. That was
17% of them -- 104 out of 619 -- with a clean gap separating those boxes from
every real detection, so they are unambiguous. They are now discarded.

Being straight about the result: **that fix changed no verdicts.** Rescanning 25
real events with it on and off gives the same answer both ways, because an event
with one bogus box also has real ones. It was worth doing, since a wrong
measurement should not feed a decision, but the number above has not moved and
this page is not going to claim it has.

So the over-flagging is still open, and the reason it is still open has not
changed: fixing it means changing how **every** incident is judged, and doing
that on a hunch could bury a real hit-and-run. Three tuning passes have already
been made against 19 pieces of feedback, which is a hint, not a measurement. It
waits for a properly labelled set of footage. Contributing clips is what builds
that set, and it is the reason the contribute button exists.

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

## Copy the footage off the USB drive before scanning

Reading footage off a USB stick is often the real bottleneck, so copying to
your internal drive first is usually faster overall. That was the whole of this
advice until 2026-08-05, when it turned into something stronger.

**Scanning directly from a Tesla USB drive has crashed Windows itself.** On the
development machine, three `DRIVER_POWER_STATE_FAILURE` blue screens happened
during large scans, minutes in. That bugcheck means a storage device stopped
answering the operating system, and the only device showing I/O retries in the
event log was the Tesla drive. A scan reads tens of gigabytes in one sustained
run, which is a far heavier load than these drives normally see.

This is not Mimir corrupting anything — nothing is written to the drive, and no
footage was lost. But a scan is the workload that provokes it.

**Mimir can now do the copying for you.** After you pick a folder, the import
screen offers **Copy footage to this PC**. It copies each file, reads it back,
and checks it byte for byte, then scans the local copy. Verification costs
about 5% on top of the copy -- roughly one minute on a 49 GB import -- which is
not a reason to skip it.

There is a checkbox to **clear the drive afterwards**. Nothing is removed until
its copy has been verified, and any file that does not match is left exactly
where it is. If some files fail, the ones that succeeded are cleared and the
rest stay, so the drive is never made to look empty while it still holds
footage you have not got a copy of.

If you have already hit the blue screen, disabling *USB selective suspend* in
Windows power settings is a known mitigation for this class of crash, and
trying a different port or cable is worth a go.

Seen on one machine so far. If it happens to you, please report it -- knowing
whether this is one bad drive or something everyone hits changes what we do
about it.

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
- **Mimir Trash needs emptying, and now you can.** Trashing a clip moves it off
  your USB drive onto this PC, which frees the stick but not the computer.
  Until 2026-08-05 nothing ever emptied that folder, so it only grew -- 2.4 GB
  on the development machine. The Trash view now shows what it is costing and
  has an **Empty Mimir Trash** button.
- **Deleting is now a real option.** The confirmation offers two things: move
  to Trash (keeps the clips, restorable), or delete -- which removes the clips,
  the Tesla event folder, and the thumbnails Mimir generated. Deleting sends
  files to the Windows Recycle Bin by default, so a mistake is recoverable the
  usual way. There is a checkbox to skip the bin and free the space
  immediately; that one really is permanent.
  - Worth knowing before you use it: a Tesla event folder usually holds several
    clips, which Mimir splits into separate incidents. The folder is only
    removed once nothing in it is still live, so deleting one incident of three
    leaves the folder until the other two go as well.
- **Windows 10/11, 64-bit only.** No macOS, no Linux, no 32-bit or ARM build.
  Requires WebView2, which is present on almost all current Windows installs.
- **Optional local AI is off by default** and is a second opinion only. It can
  never override hard local evidence, and a scan never waits on it.
- **No accounts, no cloud sync, no payments** in this beta.

## Things that are deliberately not automatic

Not bugs -- design decisions, listed because they surprise people:

- **Nothing is uploaded unless you press a button.** Scanning is entirely local.
  Feedback and footage contributions are separate, explicit, per-item actions.
- **Scanning copies your clips, and never edits them.** Footage on another drive
  is copied into your Mimir library as the scan reaches it, so the scan reads
  from your own disk rather than the drive -- reading video off a USB stick is
  the slowest part, and on some machines the sustained load has crashed Windows
  partway through. Footage already on the library's drive is scanned where it
  sits. Your originals are left alone unless you tick *Clear the drive as it
  goes*, which is off by default and only removes an event after its copy has
  been checked byte for byte and scanned.
- **Contributions ask for consent details the first time.** Footage of other
  people carries obligations that feedback text does not, so the first
  contribution walks through who you are and on what basis you hold the
  footage. It is remembered afterwards.

  If you tried to contribute a clip before 2026-08-05 and got *"That
  contribution package could not be written"*, that was our bug, not anything
  you did. Contributing had never worked from an installed build -- a file the
  packaging step needed was missing from the installer, and because sending
  feedback did not use that file, feedback kept working and hid it. Fixed, and
  the build now refuses to produce an installer with that file missing. Please
  do try again.

## What Mimir will not do

- Prove that physical contact occurred. Overlapping shapes in an image are not
  evidence of touching, and Mimir says so rather than pretending otherwise.
- Identify people or read number plates.
- Decide anything for you. Every status is editable and your correction is
  kept separate from Mimir's original answer.
