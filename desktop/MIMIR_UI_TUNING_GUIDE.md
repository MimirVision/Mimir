# Mimir UI Tuning Guide

This guide explains where to make visual changes safely.

## Where Colors And Styles Are Changed

Global colors are in:

```text
src\index.css
```

Look for CSS variables:

```css
--mimir-bg
--mimir-bg-depth
--mimir-surface
--mimir-text
--mimir-text-muted
--mimir-border
--mimir-status-red
--mimir-status-amber
--mimir-status-green
--mimir-status-slate
```

Most components use Tailwind classes plus these variables.

Example:

```tsx
className="bg-[var(--mimir-bg-depth)] text-[var(--mimir-text)]"
```

If you want the whole app brighter or darker, start with `index.css`.

## Where Layout Lives

Main layout:

```text
src\App.tsx
```

Review/library layout:

```text
src\components\IncidentLibraryView.tsx
```

Incident viewer layout:

```text
src\components\IncidentViewerScreen.tsx
```

Import screen layout:

```text
src\components\ImportPanel.tsx
```

## Changing Card Sizes

Incident cards are in:

```text
src\components\IncidentLibraryView.tsx
```

Look for the grid:

```tsx
grid-cols-[repeat(auto-fill,minmax(238px,1fr))]
```

To make cards wider:

```tsx
grid-cols-[repeat(auto-fill,minmax(250px,1fr))]
```

To fit more cards per row:

```tsx
grid-cols-[repeat(auto-fill,minmax(180px,1fr))]
```

Keep cards large enough that badges and buttons do not overlap.

## Changing Thumbnail Size

Review card thumbnails use `IncidentImage` in:

```text
src\components\IncidentLibraryView.tsx
```

Look for:

```tsx
large ? 'min-h-[320px]' : 'h-[128px]'
```

To make review thumbnails taller:

```tsx
'h-[150px]'
```

To make them more compact:

```tsx
'h-[110px]'
```

Viewer media size is in `IncidentViewerScreen.tsx`, inside `ViewerMedia`.

## Tabs And Filters

Review tabs are in:

```text
src\components\IncidentLibraryView.tsx
```

The tab type is:

```ts
type LibraryFilter = 'IMPORTANT' | 'REVIEW' | 'IGNORE' | 'ALL' | 'TRASH'
```

The active filter state is:

```ts
const [filter, setFilter] = useState<LibraryFilter>('ALL')
```

The visible incidents are calculated in `visibleIncidents`.

If you add a filter, update:

- `LibraryFilter`
- counts
- `visibleIncidents`
- `FilterChip` buttons
- empty-state copy if needed

## In-App Library Sections

Library sections are in `IncidentLibraryView.tsx`.

Look for:

```tsx
<LibrarySection title="Important" ... />
<LibrarySection title="Review" ... />
<LibrarySection title="Ignore" ... />
<LibrarySection title="Trash" ... />
```

These sections read from the latest session. They do not scan folders directly.

## Simplifying Clutter

Good places to simplify:

- Incident cards: remove secondary badges before removing status or Files.
- Details panel: keep technical fields inside `<details>`.
- Files drawer: keep paths there, not on cards.
- Review action panel: keep status actions visible; hide rare details lower down.

Avoid:

- Showing full file paths on cards.
- Large raw JSON blocks in the main workflow.
- Adding more dashboard panels above the incident grid.

## Button And Badge Styling

Severity badge colors come from helper functions like:

```ts
severityClass()
```

Storage badge styling is in:

```ts
storageBadgeClass()
```

Keep destructive wording as:

```text
Move to Mimir Trash
```

Do not label this as Delete unless the behavior changes.

## Testing With Large Scan Results

Use a real `latest_session.json` with many incidents.

Backend:

```powershell
cd C:\MimirDev\backend
.venv\Scripts\python.exe mimir_core_v2_scan.py --input "D:\TeslaCam" --mode balanced
```

Frontend:

```powershell
cd C:\MimirDev\desktop
npm run build
npm run desktop:dev
```

Things to check:

- 150+ incident cards scroll smoothly.
- Ignore tab is usable.
- Trash tab is usable.
- Files drawer opens quickly.
- Viewer opens without layout overlap.
- Text does not overflow buttons or cards.

## Safe UI Change Checklist

After UI edits:

```powershell
cd C:\MimirDev\desktop
npm run build
npm run type-check
```

If you changed Tauri commands:

```powershell
cd C:\MimirDev\desktop\src-tauri
cargo check
```

Then click through:

- Import screen
- Scan progress
- Review tabs
- Incident viewer
- Files drawer
- Library page
- AI feedback panel

