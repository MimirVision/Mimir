# Mimir

**Find the moments worth watching in your Tesla's footage.**

Sentry Mode records everything. A week of it is hundreds of clips, and almost
all of them are nothing — a cat, a passing car, a shadow moving across the
garage. Mimir watches them for you and sorts them, so you look at ten clips
instead of seven hundred.

It runs entirely on your own PC. Your footage is never uploaded anywhere unless
you deliberately choose to send some.

> **Free public beta.** It works, it is useful, and it gets things wrong in
> ways described honestly below. Please read [What to expect](#what-to-expect)
> before you decide whether it is worth your evening.

---

## Download

**[⬇ Download Mimir for Windows](https://github.com/QIKenway/Mimir/releases/latest/download/MimirSetup.exe)**

About 197 MB. Windows 10 or 11, 64-bit.

---

## Installing

It is a normal Windows installer, with one ugly moment in the middle that is
worth knowing about in advance.

### 1. Run the file you downloaded

Double-click `MimirSetup.exe`.

### 2. Windows will try to stop you. This is expected.

You will see a blue box: **"Windows protected your PC"**.

Click **More info**, then **Run anyway**.

That warning is not a false alarm and I am not going to tell you to ignore
warnings. Here is exactly what it means: Windows checks whether an installer
was signed by a verified publisher. Mimir is not signed yet, because a code
signing certificate costs several hundred dollars a year and this is a free
beta. So Windows correctly reports that it does not know who made this. It is
telling you it cannot vouch for the file — not that the file is malicious.

If that is not good enough for you, that is a completely reasonable position.
You can [build it yourself from source](#building-from-source) instead.

### 3. Click through the installer

Defaults are fine. It installs to your user folder, so it does not need
administrator rights.

If your PC is missing Microsoft's WebView2 runtime, the installer fetches it
automatically — this needs an internet connection, and takes a moment. Windows
11 already has it. Most Windows 10 machines do too, because Edge installs it.

### 4. Launch Mimir

That is it. No account, no sign-up, no licence key, nothing to configure.

---

## Your first scan

### 1. Get your footage onto your PC first

**Copy the folders off the USB stick before scanning.** Do not scan directly
from the drive still plugged into your car's dashcam port.

This matters more than it sounds like it should:

- It is genuinely faster. Reading video off a USB stick is usually the slowest
  part of the whole process.
- Sustained heavy reading is hard on these drives. On at least one machine,
  scanning straight from the Tesla USB drive has caused Windows itself to crash
  — a `DRIVER_POWER_STATE_FAILURE` blue screen, which is Windows' way of saying
  a storage device stopped answering. Copying the files first avoids the whole
  problem.

Copy the `TeslaCam` folder — or just the `SentryClips` folder inside it — to
somewhere on your internal drive. Make sure you have room; a full drive's worth
of footage can be 50 GB or more.

### 2. Point Mimir at the folder and scan

Open Mimir, choose the folder you just copied, and start the scan. The import
screen tells you what hardware it found before you commit to anything.

**How long it takes:** roughly **25 minutes for a full 679-clip dump** on a
machine with a DirectX 12 graphics card. Without one, object detection falls
back to the processor and runs about ten times slower — budget hours, not
minutes. Mimir tells you which one you are getting up front.

### 3. Review what it found

Everything is sorted into **Important**, **Review**, and **Ignore**. Start at
the top. Every clip has a timeline showing where Mimir thinks something
happened, so you can jump straight there instead of watching the whole minute.

You can change any verdict. Your correction is kept separately from Mimir's
original answer — it never quietly overwrites what the scan said.

### 4. Tell it when it is wrong

This is the part that actually matters, and it takes ten seconds.

When Mimir flags something that is nothing, hit the feedback button and say so.
That is the single most valuable thing a beta tester can do, because the
detector cannot improve without examples of what it got wrong. A clip Mimir
misjudged is worth more than a hundred it got right.

If you are willing to also send the clip itself, there is a separate,
explicit option for that. It is encrypted on your machine before it goes
anywhere. It is never automatic and never bundled into the feedback button.

---

## What to expect

### It over-flags

**Expect it to call things Important that are not.** Treat Important as *"look
at this"*, not *"something happened"*.

Of the beta feedback so far, 18 of 19 clips Mimir rated Important were rated
lower by the person who actually watched them. That is not a measured error
rate — people write in when something is wrong, not when it is right, so the
sample is skewed by construction. But the direction is not in doubt.

Two rules were tightened on 5 August 2026 after reading that feedback back.
Replayed against it, the change removes 17 of the 18 Important calls a human
rejected while keeping the one they agreed with. Those clips drop to Review,
not Ignore — nothing gets hidden from you.

**It should over-flag less than it did. It still over-flags.**

### What it will not do

- **Prove that contact happened.** Two shapes overlapping in a video is not
  evidence that anything touched. Mimir says so rather than pretending
  otherwise.
- **Identify people or read number plates.** Deliberately, permanently.
- **Decide anything for you.** Every verdict is yours to change.
- **Delete or move anything on its own.** File actions are separate commands
  you invoke yourself.

[`KNOWN_ISSUES.md`](desktop/release_assets/KNOWN_ISSUES.md) has the full list,
including the smaller annoyances.

---

## Your footage stays yours

- **Scanning is entirely local.** No account, no cloud, no telemetry, no
  analytics. Nothing about your footage leaves your PC as a side effect of
  using Mimir.
- **Two things can leave, both only when you press a button:** written feedback
  about a verdict, and — separately and explicitly — a clip you choose to
  contribute. Both are encrypted on your machine before they are sent.
- **Contributions ask for consent details the first time**, because footage of
  other people carries obligations that a text comment does not.
- **Nothing is deleted.** Mimir never removes a file you did not tell it to.

---

## Requirements

|  |  |
|---|---|
| Operating system | Windows 10 or 11, 64-bit |
| Graphics | A DirectX 12 GPU is optional but makes it roughly 10× faster |
| Disk | ~325 MB installed, plus room for the footage you copy over |
| Internet | Only to fetch WebView2 during install if your PC lacks it, and if you choose to send feedback |
| Account | None |

No macOS or Linux build. No 32-bit or ARM build.

---

## Something went wrong, or it got something wrong

- **A wrong verdict:** use the feedback button in the app. It carries the
  context needed to actually diagnose it.
- **A bug, a crash, or anything else:** open an
  [issue](https://github.com/QIKenway/Mimir/issues), or email
  <feedback.mimir@gmail.com>.

Reports that Mimir was wrong are the most useful thing this beta produces.
Please do send them.

---

## Building from source

If you would rather not run an unsigned installer, or you want to work on it:

You will need Node 20+, Rust, Python 3.12, and `age`:

```powershell
winget install --id FiloSottile.age --exact
```

The detection engine is Python, compiled into bundled executables at build
time, so it needs its own environment first. It has to be a **clean** one —
the build deliberately refuses to run if training-only packages such as
PyTorch are installed, so that they cannot end up inside a shipped binary.

```powershell
git clone https://github.com/QIKenway/Mimir.git
cd Mimir\backend
python -m venv .venv-runtime
.\.venv-runtime\Scripts\python.exe -m pip install -r requirements-core-v2.txt
.\.venv-runtime\Scripts\python.exe -m pip install pyinstaller
```

Then the app itself:

```powershell
cd ..\desktop
npm install
npm run desktop:build:internal
```

The installer lands in `desktop\src-tauri\target\release\bundle\nsis\`.

Use `desktop:build:internal`, not `desktop:build`. The latter signs the
auto-update manifest and needs a private key that is not in this repo, so it
will stop and tell you the key is missing. The internal build skips signing;
the only thing it costs you is that the result cannot serve auto-updates,
which does not matter for a build you made yourself.

The first build takes a while — PyInstaller has to package the detection
engine and the ~120 MB object detection model.

[`ARCHITECTURE.md`](ARCHITECTURE.md) explains the layout: the desktop app, the
detection backend, the intake Worker, and the contracts that span them.

---

## Licence and trademarks

Not affiliated with, endorsed by, or sponsored by Tesla, Inc. Tesla, Sentry
Mode, and TeslaCam are trademarks of Tesla, Inc.
