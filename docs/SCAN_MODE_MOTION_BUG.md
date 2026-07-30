# Scan modes disagree because motion scores depend on sampling rate

Status: **diagnosed, not fixed.** Reported from real use: the `fast` scan mode
locates impacts better than `balanced` or `thorough`.

## Reproduction

`C:\Mimir\Test\reddit 3.mp4` (29s). The real impact is at ~16.3s.

| mode | sampled frames | `max_motion_score` | `visual_contact_time_sec` | primary key moment |
|------|----------------|--------------------|---------------------------|--------------------|
| fast (1 fps) | 30 | 0.5706 | 16.0 | **16.267** (correct) |
| balanced (2 fps) | 59 | 0.4404 | 15.5 | 15.033 (~1.2s early) |
| thorough (4 fps) | 110 | 0.3338 | 23.467 | **26.0** (~10s late) |

## Root cause

`motion_analysis.analyze_motion` scores motion as `cv2.absdiff` between
consecutive **sampled** frames:

```python
diff = cv2.absdiff(previous_gray, gray)
global_score = min(float(diff.mean()) / 55.0, 1.0)
```

The score is therefore "visual change per sampling interval", which is a
different physical quantity in every scan mode:

- at 1 fps a sharp impact lands inside a single 1.0s delta and spikes hard;
- at 4 fps the same impact is spread across four 0.25s deltas, each small.

Note the `max_motion_score` column above *decreases* as sampling gets denser.
Once the impact no longer stands out from background motion, candidate
selection in `_first_visual_contact_sample` picks a different, later event, and
the refiner faithfully refines the wrong moment. The refiner is not at fault.

This is also why raising the `frame_sampler` caps (so balanced/thorough deliver
their advertised fps on clips longer than ~30s) makes the problem *worse* on
real 60s Sentry clips, even though denser sampling genuinely helps object
detection recall.

## Attempted fix and why it was reverted

Normalising scores to change-per-1.0s (`ratio ** exponent`, reference 1.0s so
the well-behaved 1 fps calibration stays numerically untouched) was tried:

| exponent | thorough timing | cross-mode severity disagreements (6 clips) |
|----------|-----------------|---------------------------------------------|
| 0.75 | 16.2 (fixed) | 2/6, including a false IMPORTANT |
| 0.40 | 26.0 (unfixed) | 1/6 |

Pixel difference saturates rather than growing linearly with time, so the
correct exponent is well below 1 -- but the value that fixes timing also
amplifies background motion enough to invent false IMPORTANT flags, and the
value that avoids that is close to a no-op. Choosing between them by eye on six
clips is overfitting, so the change was reverted rather than shipped.

**This needs the labelled set (`mimir_core_v2_label.py`) to calibrate against.**
With 30-50 human-labelled clips, the exponent becomes a measured choice instead
of a guess.

## Recommended fix (preferred over tuning)

Decouple motion sampling from detection sampling. Motion analysis wants a
roughly fixed ~1.0s interval regardless of mode; object detection wants the
densest sampling the mode allows. They currently share one frame set, which
forces one signal to be wrong.

Concretely: keep sampling frames densely for the detector, but compute motion
deltas against a ~1.0s-spaced subsequence (or accumulate deltas to a 1.0s
window). Motion scores then mean the same thing in every mode by construction,
with no magic exponent, and denser modes get strictly better object recall
without degrading impact timing.

## Interim guidance

Use `fast` for impact timing. Related rate-dependence bugs already fixed:

- `_dwell_flags` used raw sampled-frame counts (commit "Fix dwell flags scaling
  with sampling rate"), guarded by `test_dwell_flags.py`.
- `_first_visual_contact_sample` required `len(segment) >= 3` raw samples
  (commit "Make visual-contact clustering rate-independent").
