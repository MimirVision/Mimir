# Mimir Landing Page

This folder is a static private beta download page for Mimir. It has no build step and no external dependencies.

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

## Local Preview

Open `index.html` directly in a browser. The page does not require npm, Vite, Tauri, or internet access.
