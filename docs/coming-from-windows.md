# Coming from Windows: the short version

Plain answers to the questions almost everyone asks in the first week.
Nothing here is a sales pitch — where Rowyn does something worse than
Windows, it says so.

## "Where is the antivirus?"

There isn't one, and your system is not unprotected — it is protected
differently:

- Apps come from Flathub as Flatpaks, which run in a sandbox with
  per-app permissions (see *Settings → Application Permissions* in the
  Hub, or KDE's Flatpak settings).
- The operating system itself is a signed image. Updates are verified
  against Rowyn's signing key before they are staged, and a bad update
  rolls back automatically (below).
- The firewall (`firewalld`) is on by default.
- Windows malware — the overwhelming majority of what antivirus exists
  to catch — cannot run on Linux at all. Linux malware exists, which is
  why the sandboxing and signed updates matter; it is rare enough that
  no consumer Linux ships a scanner, and Rowyn follows that norm
  honestly rather than installing a placebo.

## "How do updates work? Can I go back?"

- Rowyn updates as a whole system image, not package by package. The
  Hub's **Updates** page shows what is staged; updates download in the
  background and apply on the next reboot.
- Every update keeps the previous system. If a new image fails its
  boot health checks three times, Rowyn quarantines it and boots the
  previous one automatically. You can also roll back deliberately from
  the Updates page or the Dashboard's recovery card.
- Your files, settings and installed Flatpaks are never part of the
  image and are never rolled back — only the operating system is.

## "Where did things go?"

| Windows | Rowyn |
| --- | --- |
| Start menu | Application Launcher (bottom-left) |
| File Explorer | Dolphin |
| Settings | System Settings, and the Rowyn Hub for OS-level things |
| Task Manager | System Monitor (Ctrl+Esc shows processes) |
| Microsoft Store | Discover and the Hub's Apps page (Flatpaks) |
| Control Panel printers | System Settings → Printers, or Hub → Work |
| `C:\Users\<you>` | `/home/<you>` (shown as your Home folder) |
| Program Files | There isn't one — apps live in the system image or in Flatpak's own store; you never manage their files |
| `.exe` installers | See below |
| OneDrive / Google Drive | KDE Online Accounts (System Settings) puts Google Drive in Dolphin; OneDrive works in the browser |

## "This Windows program won't run"

Double-clicking a `.exe` on Rowyn opens the exe handler, which is
honest about the three possible answers:

1. **There is a Linux app for that** — it names it and installs it from
   Flathub (Word → LibreOffice, Outlook → Betterbird, and so on).
2. **It can run inside Bottles** — a contained Windows compatibility
   environment. Many utilities and older games work this way; the
   handler offers it explicitly rather than pretending it is native.
3. **It cannot run here** — some things (kernel-level anti-cheat,
   Adobe's current suite, anything with a Windows driver) genuinely
   have no path. The handler says so instead of wasting your time.

## "I downloaded a .deb file" (Chrome, Zoom, …)

Rowyn installs graphical apps as Flatpaks, so a `.deb` from a website
does not install by double-clicking — on purpose: Flatpaks are
sandboxed, update in one place, and cannot scatter files across the
signed system image. Almost every app offered as a `.deb` download is
also on Flathub: open the Hub's **Apps** page (or Discover) and search
for it there. Opening a `.deb` in Dolphin explains this and points you
at the same place. Command-line tools belong in Distrobox, which the
Hub's Development page sets up.
