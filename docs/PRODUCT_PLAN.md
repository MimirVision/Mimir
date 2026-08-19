# Making Mimir better

Written 2026-08-08, after going through the app, the backend, and every scan
on this machine. Ordered by what would change the product most, not by what is
easiest.

Every number here is measured. Where something is a guess, it says so.

---

## The one problem

**Mimir surfaces 42% of a week's footage and calls it triage.**

Re-resolving all 4,173 incidents on this machine through the current rules:

| | out of 656 events |
|---|---|
| Ignored for you | 378 |
| **Put in front of you** | **278** |
| Marked Important | **0** |

Everything else in this document is worth less than fixing that. A tool that
turns 656 clips into 278 is a tool people try once.

The mechanism is known precisely: something counts as "close", which sets
`contact_level` MEDIUM, which sets `possible_contact`, which blocks the
`normal_traffic` rule that exists to say "this is just a car park". The rule
written to prevent exactly this is held shut by the thing it was written to
prevent.

What was *not* known was why "close" fires everywhere. The answer here used to
be the car's own bodywork filling the bottom of a repeater frame. **Measured
2026-08-17, that is wrong**: only ~12% of close-vehicle detections fall inside
the ego region. Two things the measurement did establish:

- On low-contrast night footage the detector sometimes returns a box covering the
  whole frame and labels it a vehicle -- 104 of 619 detections, separated from
  every real one by an empty band, so removing them is not a judgement call.
  Fixed, and honestly: **it changed no verdicts** on a 25-event A/B, because an
  event with one such box also has real ones.
- A third theory, that `_contact_level_from_motion` ANDs proximity and motion
  without requiring them to be the same object, was **measured on 2026-08-17 and
  is also wrong**. Patching the real functions and comparing motion regions
  against the close-object boxes across 31 cameras that reached MEDIUM/HIGH: the
  motion covers a median **94%** of the close object, 26 of 31 are above half,
  and exactly one is below 10%. The close thing genuinely is the thing moving.

Three explanations proposed, three measured false: the ego vehicle, the
frame-filling boxes, and un-co-located motion. What is left is uncomfortable but
probably right -- "something large is close to the camera and it is moving" is a
truthful description of a car driving past a parked car in a repeater view. The
signal is real; it just does not mean contact. Separating those two needs
examples of both, which is to say **labelled footage**, and no amount of
re-reading the code substitutes for it. Which brings us to the thing that
actually blocks everything.

---

## 1. The labelling loop exists and produces nothing

`save_manual_status` writes `user_status` and `manual_status_override` into
`session.json`. Every correction a human makes is already persisted, in a
structured, harvestable form.

Across **53 sessions and 4,837 incidents on this machine: zero corrections.
Zero notes.**

The machinery is complete and the pipe is empty. That is not a user failure —
correcting a verdict currently costs a click and returns nothing, so nobody
does it. Meanwhile the model card demands a locked set of ≥750 groups with
≥300 positives, and the release plan calls the corpus supply line dead.

**It is not dead. It is unbuilt at the last inch.**

### What to do

**1a. Make the verdict a two-key decision, not an optional edit.**
In the viewer, `→` = agree, `↓` = should be lower, `↑` = should be higher.
Reviewing already means watching each clip and forming that judgement; the
only thing missing is capturing it. 4,837 incidents already reviewed would
have been 4,837 labels.

**1b. Harvest them.** A command that walks every session, pulls every
`user_status` alongside its stored `local_evidence`, and writes an evaluation
set. The evidence is already in the session — no re-scan needed. This is
perhaps 150 lines.

**1c. Show the user what their corrections bought.** "You have corrected 340
clips. Mimir is now right about 8 in 10." Nothing else will sustain the
behaviour.

**1d. Then fix the ego-vehicle bug** with a real before/after, and delete the
over-flagging paragraph from the download page honestly.

This is the single highest-leverage sequence in the product. It converts the
thing users already do into the thing the model needs.

---

## 2. What people actually want that Mimir does not do

Mimir answers *"which clips are interesting?"*. People with a damaged car have
a different question: **"what happened, and can I prove it?"**

### 2a. An evidence packet

Someone whose car was hit needs to hand something to an insurer or the police.
Today they get `export_incident_report` and a folder of MP4s.

What they want is one file: every camera angle for the event, the timestamp,
the location if the clip has it, a contact sheet of the key moment, and a
plain-language summary of what Mimir observed — with its uncertainty stated,
because overclaiming in a document that reaches an insurer is worse than
useless.

This is the feature most likely to make someone tell another Tesla owner
about Mimir.

### 2b. Knowing without looking

Sentry footage is only checked after you notice damage. By then the stick may
have overwritten the event.

Mimir already detects drive insertion (`TeslaCamDriveEvent`). The gap between
that and *"plug in, walk away, get told"* is small: auto-scan on insert, and a
notification when the scan finds something. That turns the product from a tool
you remember to use into one that tells you.

### 2c. More than one car, more than one stick

There is one library and no concept of which vehicle footage came from.
Households with two Teslas exist, and so do people who rotate two USB drives.

### 2d. Finding things later

Sessions are siloed. "Show me everything from the week of the 14th", "every
event in this car park", "everything I marked Important this year" — none are
possible. The library is per-scan, and the natural mental model is per-car and
per-date.

---

## 3. Where the app will hurt as it grows

| | Measured | Why it matters |
|---|---|---|
| `IncidentViewerScreen.tsx` | **2,754 lines** | One component holding playback, evidence, feedback, contribution, notes, file actions. Every change risks the others. |
| `IncidentLibraryView.tsx` | 1,572 lines | Renders all incidents unvirtualised — 656 cards at once today. |
| `evidence_extractor.py` | **1,981 lines** | Where the over-flagging lives. Severity rules are duplicated across three code paths; that duplication has caused a real bug already. |
| Component tests | **none** | The pure-logic layer has 141 tests. The three biggest components have zero, and every recent user-visible bug was in them. |
| Settings | **does not exist** | Preferences are inline `<details>` and `localStorage`. |

None of these are urgent on their own. All of them make the next twenty
changes slower, and two of them (virtualisation, the viewer split) will be
forced by any of the features in §2.

---

## 4. Trust, beyond accuracy

Accuracy is one half. The other half is that the product never surprises you
with what it did to your files.

- **Crash reporting does not exist**, by policy. That policy was right for a
  local-only beta and is wrong the moment there are testers: the last three
  real bugs were found by a person, not a test. Opt-in, EU-hosted, disclosed.
- **The scan cannot resume.** Interrupt a 25-minute scan and it starts over.
  The streamed import made the *copy* resumable; the scan itself is not.
- **There is no undo for a batch action.** Move 200 clips to Trash by
  mistake and the only route back is 200 restores.

---

## 5. Sequencing

Not by size — by what unblocks what.

**Now — the loop.** §1a–1c. Two-key verdicts, a harvest command, and a counter
that shows the user their corrections mattered. Nothing else needs finishing
first, and everything else gets better once this exists.

**Next — prove it.** Once a few hundred labels exist: fix the ego-vehicle bug,
measure before and after, and either delete the over-flagging warning or
publish the new number.

**Then — the reason to tell a friend.** The evidence packet (§2a), then
auto-scan and notify (§2b).

**Alongside — pay down what blocks the above.** Split the viewer before adding
anything to it. Virtualise the library before multi-session views. Add crash
reporting before there are more than ten testers.

**Later — reach.** Multi-vehicle, cross-session search, mobile review.

---

## What I would not do

- **Cloud scanning, yet.** It is measured at 4–7× slower than local for anyone
  under ~100 Mbps up, and it costs the privacy claim that is currently the
  product's clearest differentiator. It only pays for CPU-only machines.
- **More detector tuning without labels.** Three unvalidated passes have been
  made against 19 selection-biased feedback labels. A fourth would be guessing
  with someone's evidence.
- **More features before the viewer is split.** At 2,754 lines it is the
  place where a careless change breaks something unrelated.
