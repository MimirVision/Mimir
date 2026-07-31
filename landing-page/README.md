# Mimir Landing Page

This folder is a static free public beta download page for Mimir. It has no build step and no external dependencies.

## Files

- `index.html`
- `styles.css`

## Sharing a Beta Download

Before sharing the folder, copy the Windows installer next to `index.html` and name it:

```text
MimirSetup.exe
```

Expected folder shape:

```text
landing-page/
  index.html
  styles.css
  README.md
  MimirSetup.exe
```

Then send the folder as a zip file or host the folder on a simple static file server.

This is fine for handing the build to someone directly, but **nothing is
counted** when the download link is a relative path. For a public beta, host the
installer as a GitHub Release asset instead.

## Public Hosting

The installer is a few hundred megabytes, which rules out the hosts that would
otherwise be obvious: Netlify caps files at 100 MB, and Vercel's bandwidth
pricing on a binary this size gets expensive quickly. A thousand downloads is
several hundred gigabytes of transfer.

Two options that work:

**GitHub Releases** (recommended). Free, 2 GB per file, no practical bandwidth
cap, and it reports a download count per asset -- which is the only way to
measure uptake without adding telemetry to the app. The source can stay private:
create a separate public repository that holds nothing but releases.

1. Create the repository and push a tag matching `package.json`
   (`v0.5.0-free-beta.1`). `.github/workflows/release.yml` builds the installer,
   runs the test suites, smoke-tests the packaged scanner, and drafts a release.
2. Open the draft, check it, and publish. Nothing is downloadable until then.
3. Point the download link in `index.html` at
   `https://github.com/OWNER/REPO/releases/latest/download/MimirSetup.exe`.
4. Serve this folder from GitHub Pages.

**Cloudflare R2** if you want your own domain. Zero egress fees, and the free
tier covers this comfortably. You lose the free download counter, so you would
need to read it from R2's own analytics instead.

## Measuring Uptake

```bash
python scripts/download_stats.py --repo OWNER/REPO
```

Records the per-asset download count with a timestamp, so the rate is visible
over time. Run it periodically. `--show` prints the history without fetching.

Downloads are the only thing measurable from here. Whether someone got past the
SmartScreen warning, installed, or scanned anything is not observable without
telemetry Mimir deliberately does not have, so read this number next to the
feedback inbox rather than on its own.

## Local Preview

Open `index.html` directly in a browser. The page does not require npm, Vite, Tauri, or internet access.
