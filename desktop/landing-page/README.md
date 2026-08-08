# Mimir Landing Page

This folder is a static free public beta download page for Mimir. It has no build step and no external dependencies.

## Files

```text
landing-page/
  index.html          markup and copy, including the hero illustration
  styles.css          all styling; tokens copied from the app's src/index.css
  main.js             scroll reveals, counters, sticky header
  mimir-mark.png      app icon, same file as src/assets/mimir-mark.png
  README.md
```

There is no hero image, after three attempts at one. A dashcam still of a dark
garage showed nothing about the software. An SVG of the review screen was a
drawing of an interface pretending to be a screenshot. A row of large figures
turned the first thing a visitor saw into a statistic.

The hero is now type, one action, and atmosphere.

### The atmosphere is doing real work

With no image, the section is otherwise a paragraph on a black rectangle, and a
page that never moves reads as unfinished. Four layers, all behind the text
rather than around it -- motion beside something someone is trying to read is
an irritation, not polish:

| Layer | What it does |
|---|---|
| `hero__aurora--a/b` | Two gradient fields drifting on 34s and 47s. Mismatched on purpose: a single loop becomes obvious within a minute of looking at it. |
| `hero__sweep` | One light pass on load, suggesting a read straight through a week of footage. It does not repeat — looping it makes a screensaver. |
| `hero__grid` | A masked 64px grid so the black is not flat. |

Elsewhere the motion is attached to something the reader is already doing:
section rules draw in on arrival, step numbers brighten in sequence so the four
steps read in order, and the download button breathes very slightly — pausing
on hover, so it is never moving under the pointer.

`prefers-reduced-motion` needs more than the blanket duration override, and
gets it. Zeroing an animation's duration freezes it at its first keyframe,
which for the sweep is a bright band across the hero; that rule cancels the
animation and the gradient outright instead.

### Requirements are behind a disclosure

System requirements, the SmartScreen explanation, and the note that it
over-flags sit inside a `<details>` under the final download button rather than
in sections of their own.

The reasoning is that a wall of specifications and a paragraph about a Windows
security warning is a poor thing to put between someone and a download -- but
both have to be one click away and both have to be honest. A tester who meets
SmartScreen with no warning just stops. One who discovers later that it
over-flags stops believing anything else here.

`<details>` rather than a modal: it works with JavaScript disabled, it is
keyboard-accessible for free, and find-in-page can still reach the contents in
most browsers.

Nothing is fetched from a third party -- no web fonts, no CDN, no analytics.
That is deliberate rather than minimalism for its own sake: the page's central
claim is that nothing about you gets sent anywhere, and a Google Fonts request
would quietly contradict it on the page making the argument. A system font
stack also needs no licence and renders with no layout shift.

### The animation has to be able to fail

Every rule that hides something is scoped to a `.js` class, set by a blocking
inline script in `<head>`. `main.js` removes that class if the reveal observer
has not reported within 1.2 s, which turns the page back into a plain visible
document.

This is not defensive theatre. `IntersectionObserver` is driven by the
rendering lifecycle, and in a browser pane that was not compositing frames a
freshly constructed observer watching an element in the middle of the viewport
fired **zero** times. Without the fallback that is a completely blank page.
The counters are settled at the same time, because a hero reading "0 clips, of
which 0 are worth opening" is not a missing effect, it is a false claim.

Test both paths by disabling JavaScript: the page should look finished, just
static.

## Sharing the folder directly

The download button points at the GitHub Release asset, so the folder works
standalone. If you would rather hand someone a zip with the installer inside,
drop `MimirSetup.exe` next to `index.html` and change that `href` to
`./MimirSetup.exe` -- but note **nothing is counted** on a relative path, and
the download count is the only uptake signal that exists.

## Public Hosting

The installer is a few hundred megabytes, which rules out the hosts that would
otherwise be obvious: Netlify caps files at 100 MB, and Vercel's bandwidth
pricing on a binary this size gets expensive quickly. A thousand downloads is
several hundred gigabytes of transfer.

Two options that work:

**GitHub Releases** (recommended). Free, 2 GB per file, no practical bandwidth
cap, and it reports a download count per asset -- which is the only way to
measure uptake without adding telemetry to the app. The repository is already public, so the release lives alongside the source.

1. Set the repository secret `TAURI_SIGNING_PRIVATE_KEY` to the contents of the
   updater private key. The workflow checks for it before building and stops
   immediately if it is missing, rather than failing 80 minutes in — and an
   unsigned build cannot serve auto-updates, so it must not go out as a release.
2. Push a tag matching `package.json` (`v0.5.0-free-beta.1`). The tag and the
   version have to agree or the build fails on purpose.
   `.github/workflows/release.yml` runs every test suite, builds the installer,
   smoke-tests the packaged scanner, writes `latest.json` for the updater, and
   drafts a release.
3. Open the draft, check it, and publish. **Nothing is downloadable until then,
   and the download link on this page 404s.**
4. Serve this folder from GitHub Pages.

The release carries four assets and each has a job: `MimirSetup.exe` is the
fixed name this page and the README link to; the versioned installer and its
`.sig` are what the updater actually downloads; `latest.json` is the manifest
the installed app polls.

**Cloudflare R2** if you want your own domain. Zero egress fees, and the free
tier covers this comfortably. You lose the free download counter, so you would
need to read it from R2's own analytics instead.

## Measuring Uptake

```bash
python scripts/download_stats.py --repo QIKenway/Mimir
```

Records the per-asset download count with a timestamp, so the rate is visible
over time. Run it periodically. `--show` prints the history without fetching.

Downloads are the only thing measurable from here. Whether someone got past the
SmartScreen warning, installed, or scanned anything is not observable without
telemetry Mimir deliberately does not have, so read this number next to the
feedback inbox rather than on its own.

## Local Preview

```bash
python -m http.server 4173 --directory desktop/landing-page
```

Then open <http://localhost:4173>. Opening `index.html` as a `file://` URL also
works, but serving it is closer to production and avoids the origin rules that
some browsers apply to local files. No npm, Vite, Tauri, or internet access
required either way.
