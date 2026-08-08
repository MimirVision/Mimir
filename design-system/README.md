# Mimir design system

One set of tokens, used by the desktop app and the download page, plus the
rules for choosing between them.

```
design-system/
  tokens.css     the tokens themselves, commented. The source of truth.
  reference.html open it in a browser to see every token rendered.
  README.md      how to choose. This file.
```

**Use it:**

```css
@import "../../design-system/tokens.css";   /* desktop/src/index.css */
@import "../../design-system/tokens.css";   /* desktop/landing-page/styles.css */
```

---

## Why this exists

The palette used to live in two hand-copied lists, one in the app and one on
the website. That is a divergence waiting to happen, and the site and the thing
you download should not drift apart.

Counting the app before writing this found the rest of the problem:

| | Found |
|---|---|
| Hardcoded font sizes (`text-[Npx]`) | **402 uses across 29 distinct sizes** |
| …including | `12px` *and* `12.5px`, `13px` *and* `13.5px` |
| Raw translucent whites (`bg-white/[0.035]` etc.) | **395 uses**, a dozen different alphas |
| Hardcoded radii | 8 |
| Hardcoded spacing | 6 |

29 font sizes is not a scale. Nobody decided that this label should be 12.5px
and that one 13px — they were typed, months apart, by someone eyeballing it.
The scale in `tokens.css` is derived from what is actually used rather than
invented, which is why it is dense at the small end: 12px appears 175 times and
13px 84 times. Mimir is a dense review tool, not a marketing site.

Radii and spacing were already consistent and needed only writing down.

---

## The one rule that matters

**Severity colours are vocabulary, not decoration.**

| Token | Means |
|---|---|
| `--mimir-severity-important` | something may have happened to your car |
| `--mimir-severity-review` | probably nothing, worth a glance |
| `--mimir-severity-ignore` | ruled out |
| `--mimir-severity-ok` | verified, succeeded, confirmed |

A user learns these in the first thirty seconds. Using red for "important
setting" or amber for "a nice highlight" teaches them that red is sometimes
just red — and from then on the red that actually matters carries less weight.

If you want emphasis and no severity is involved, use `--mimir-accent` or a
heavier text token.

The older `--mimir-status-red` / `-amber` / `-slate` / `-green` names still
work and always will; they are aliases. Prefer the semantic names in new code:
`--mimir-status-red` says what it looks like, `--mimir-severity-important` says
what it means.

---

## Choosing

### Backgrounds

Stack them in order. Each step is a level of nesting, not a mood.

| Token | Where |
|---|---|
| `--mimir-bg` | the page |
| `--mimir-bg-depth` | recessed areas, alternating sections |
| `--mimir-surface` | cards, panels |
| `--mimir-surface-soft` | raised inside a surface |
| `--mimir-surface-muted` | inputs, chips, pressed states |

Use `--mimir-tint-*` instead when the element sits on something unknown — a
translucent surface adapts, a solid one produces a visible rectangle over a
gradient.

Mimir is **dark only**. That is a product decision, not an unfinished one: it
is used to look at night-time dashcam footage, and a light theme would make the
footage the darkest thing on the screen.

### Text

Three weights, and three is the limit. A fourth grey is always someone failing
to decide between two that already exist.

`--mimir-text` for what you are reading, `--mimir-text-muted` for supporting
copy, `--mimir-text-subtle` for labels and metadata.

Primary text is `#f4f1e9`, not `#ffffff`. Pure white on near-black vibrates.

### Type

| Token | px | Typical use |
|---|---|---|
| `--mimir-text-2xs` | 11 | badge labels, table headers |
| `--mimir-text-xs` | 12 | dense UI — the workhorse |
| `--mimir-text-sm` | 13 | default body inside panels |
| `--mimir-text-base` | 14 | comfortable body |
| `--mimir-text-md` | 15 | lead paragraphs |
| `--mimir-text-lg` | 17 | section titles |
| `--mimir-text-xl` | 22 | screen titles |
| `--mimir-text-2xl` | 30 | page headings |
| `--mimir-text-3xl` | 46 | display |
| `--mimir-text-4xl` | 64 | figures, hero numerals |

Pair large sizes with `--mimir-leading-tight` and
`--mimir-tracking-display`; small sizes with `--mimir-leading-normal` and no
tracking. `--mimir-tracking-caps` is for uppercase eyebrows only — never for
sentence case.

If a size is not on the scale, the answer is one of the ones that is.

### Motion

One curve, `--mimir-ease`, for almost everything. A screen where different
elements decelerate differently feels unfinished even when nobody can say why.

`--mimir-duration-fast` for colour and hover, `--mimir-duration` as the
default, `--mimir-duration-slow` for size and position.

Durations collapse to near-zero under `prefers-reduced-motion` automatically.
**That is not sufficient on its own for anything with a moving background** —
zeroing a duration freezes an animation on its first keyframe, and for a
sweeping gradient that leaves a bright band on screen. Cancel those explicitly.
The landing page hero does this; copy that pattern.

---

## Accessibility floor

Every text-on-background pair in the system clears WCAG AA (4.5:1). Measured:

| | Contrast on `--mimir-bg` |
|---|---|
| `--mimir-text` | 18.0:1 |
| `--mimir-text-muted` | 7.4:1 |
| `--mimir-text-subtle` | 5.2:1 |
| `--mimir-accent` | 9.5:1 |

The severity colours are used as dots and borders rather than as text, so they
answer to the 3:1 non-text floor. All four clear it: important 6.0, review 8.2,
ignore 5.9, ok 8.1.

`--mimir-text-subtle` at 5.2:1 is the floor of the whole system. Do not
introduce anything dimmer, and do not use it below 11px.

These are computed, not estimated — `design-system/check_contrast.py` recomputes
every pair from `tokens.css` and fails if a documented figure has drifted. It
caught this table claiming 16.1:1 for `--mimir-text` when the real number is
18.0:1.

Focus is `2px solid var(--mimir-accent)` at `3px` offset, set globally. Do not
remove it per-component.

---

## Adding a token

Don't, if an existing one is within a hair. Two tokens that differ by 0.5px is
how the 29 font sizes happened.

If you genuinely need one: add it to `tokens.css` with a comment saying what
job it does, add it to `reference.html` so it is visible, and say in the pull
request which existing token you considered first and why it did not fit.
