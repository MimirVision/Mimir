# Mimir

**Find the moments worth watching in your Tesla's footage.**

Sentry Mode records everything. A week of it is hundreds of clips, and nearly
all of them are nothing — a cat, a passing car, a shadow across the garage.
Mimir watches them for you and sorts them, so you look at the handful that
matter instead of all of them.

Everything runs on your own PC. Nothing is uploaded.

<br>

<div align="center">

### [⬇&nbsp; Download Mimir for Windows](https://github.com/MimirVision/Mimir/releases/latest/download/MimirSetup.exe)

197 MB · Windows 10 or 11 · free public beta

</div>

<br>

---

## Installing

Double-click `MimirSetup.exe` and click through it. Defaults are fine, and it
does not need administrator rights.

Windows will show a blue **"Windows protected your PC"** box. Click **More
info**, then **Run anyway**. Mimir isn't code-signed yet — a certificate costs
several hundred a year — so Windows can't tell you who made it. If you'd rather
not, you can [build it yourself](#building-from-source).

That's it. No account, no licence key, nothing to set up.

---

## Your first scan

**Plug in the Tesla USB stick, pick the folder, press Scan.**

Mimir copies the footage to your PC as it goes, because reading video off a USB
stick is slow and hard on the drive. There's a checkbox to empty the stick as it
finishes with each clip, so you can put it straight back in the car. It's off
unless you turn it on, and nothing is removed until the copy has been checked.

A full week of footage takes about 25 minutes with a graphics card, or a few
hours without one. Mimir tells you which you're getting before it starts.

**Then review what it found.** Clips are sorted into Important, Review and
Ignore. Each one has a timeline marking where Mimir thinks something happened,
so you can jump straight there. Change any verdict you disagree with.

**And tell it when it's wrong.** Changing a verdict is how — one key, `I`, `R`
or `G`. That correction is what teaches the detector, and it's the most useful
thing you can do. Agreeing is kept on your machine and not sent.

---

## What to expect

It's a beta, and it over-flags. Treat **Important** as *"have a look at this"*
rather than *"something happened"*, and don't assume **Ignore** means nothing
did — if you know something happened, go and look for it yourself.

Mimir sorts your footage. It doesn't prove anything, it won't identify people or
read number plates, and it never moves or deletes a file unless you tell it to.

[Known issues](desktop/release_assets/KNOWN_ISSUES.md) has the details.

---

## Your footage stays yours

No account, no cloud, no telemetry. Scanning happens entirely on your machine.

Two things can leave. When you *disagree* with a verdict, that correction is
sent — what Mimir said, what you said, and why it decided that. Agreeing is kept
on your machine. Mimir asks the first time and you can stop whenever you like.
Corrections contain no video.

A clip is only sent if you tick *Send the clip too* on that incident. Both are
encrypted before they leave your machine.

---

## Requirements

|  |  |
|---|---|
| Operating system | Windows 10 or 11, 64-bit |
| Graphics | A DirectX 12 GPU is optional, but roughly 10× faster |
| Disk | ~325 MB, plus room for the footage you copy over |
| Internet | Only to fetch WebView2 during install, if your PC doesn't have it |

No macOS, Linux, 32-bit or ARM build.

---

## Something went wrong

For a wrong verdict, just change it in the app — that carries the context needed
to diagnose it. For anything else, open an
[issue](https://github.com/MimirVision/Mimir/issues) or email
<hello@mimirvision.com>.

---

## Building from source

You'll need Node 20+, Rust, Python 3.12, and `age`:

```powershell
winget install --id FiloSottile.age --exact
```

The detection engine is Python, bundled into executables at build time, so it
needs its own clean environment first:

```powershell
git clone https://github.com/MimirVision/Mimir.git
cd Mimir\backend
python -m venv .venv-runtime
.\.venv-runtime\Scripts\python.exe -m pip install -r requirements-core-v2.txt
.\.venv-runtime\Scripts\python.exe -m pip install pyinstaller
```

Then the app:

```powershell
cd ..\desktop
npm install
npm run desktop:build:internal
```

The installer lands in `desktop\src-tauri\target\release\bundle\nsis\`. The
first build takes a while — it has to package the detection engine and a
120 MB model.

[ARCHITECTURE.md](ARCHITECTURE.md) explains how the pieces fit together.

---

## Terms

[docs/TERMS.md](docs/TERMS.md) covers the beta. The part worth knowing: you need
the right to any clip you choose to send, which for dashcam footage is worth a
moment's thought.

Not affiliated with, endorsed by, or sponsored by Tesla, Inc. Tesla, Sentry Mode
and TeslaCam are trademarks of Tesla, Inc.
