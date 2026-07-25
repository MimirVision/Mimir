# System Requirements

Mimir runs entirely on your PC. There is no cloud component today, so
everything it needs has to be either bundled in the installer or already on
your machine.

## What's required

- **Windows 10 (64-bit) or Windows 11.** The installer only builds a 64-bit
  package; there is no 32-bit or ARM build.
- **~350 MB of free disk space** for the install itself (the app bundles its
  own detector model and a full offline copy of the Microsoft Edge WebView2
  runtime, so it doesn't need to download anything during setup).
- **Additional free space if you use Move to Library / Move to Trash** on
  footage that lives on a different drive than your library folder (default:
  `%USERPROFILE%\Videos\Mimir Library`) -- those actions copy the file, so
  budget for however much of your footage you expect to keep.

## What's explicitly NOT required

- **No internet connection**, for either installing or scanning. Everything
  Mimir needs -- the detector model, the WebView2 runtime -- ships inside the
  installer.
- **No account, no sign-in, no cloud storage.**
- **No GPU.** Detection runs on CPU if no compatible GPU is found. A
  DirectX12-capable GPU (most GPUs from the last several years, including
  integrated ones) speeds up scanning but isn't required.
- **No Ollama or any local AI install.** The optional "Labs" second-opinion
  feature uses Ollama if you turn it on, but the core scan and review flow
  never requires it.

## Honest unknowns

- **RAM**: not precisely profiled yet. Video decoding plus the detector model
  plus normal OS/browser overhead means 8 GB is a reasonable floor; more
  helps if you're doing other memory-heavy things at the same time. Don't
  treat this as a measured number -- it's a reasonable estimate, not a
  benchmark result.
- **The installer is unsigned** for this beta. Windows SmartScreen will show
  an "unrecognized app" warning on first run -- choose the advanced option to
  continue. This is expected, not a sign anything is wrong.
