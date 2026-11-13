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
| OneDrive / Google Drive | KDE Online Accounts (System Settings) puts Google Drive in Dolphin; OneDrive: see "What about OneDrive?" below |

## "What about OneDrive?"

Microsoft does not make a OneDrive app for Linux, and Rowyn will not
pretend otherwise. There are three honest routes, in order of how most
converts use them:

1. **The web app.** onedrive.live.com in Brave works fully — view,
   edit (Office web apps), upload, download. For occasional use this
   is all you need.
2. **rclone, as a mounted drive.** `rclone` (in a Distrobox terminal:
   `sudo apt install rclone`, then `rclone config` and pick OneDrive)
   can mount your OneDrive as a normal folder — files open from
   Dolphin and download on demand, much like OneDrive's
   files-on-demand on Windows. `rclone mount` at login is a common
   setup; rclone's own docs walk through it.
3. **rclone, as two-way sync.** `rclone bisync` keeps a local folder
   and OneDrive in step in both directions if you want real local
   copies that keep working offline.

Two distinctions converts get tripped by:

- **Sync is not import.** Rowyn's MoveIn page copies your Windows
  profile *once* — documents, pictures, browser bookmarks — into
  "From Windows" in your Home folder. Nothing keeps it in step
  afterwards. If a file lives in OneDrive and you edit it in both
  places, use one of the rclone routes, not repeated imports.
- **A mount is not a copy.** With `rclone mount`, closing the laptop
  lid offline means the files are not there. With `bisync`, they are —
  at the cost of the disk space, exactly like choosing "Always keep on
  this device" on Windows.

Google Drive needs none of this: KDE Online Accounts (System Settings)
adds it to Dolphin natively.

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

Rowyn still installs graphical apps as Flatpaks first: Flatpaks are
sandboxed, update in one place, and cannot scatter files across the
signed system image. Almost every app offered as a `.deb` download is
also on Flathub — open the Hub's **Apps** page (or Discover) and
search for it there first.

When an app only exists as a `.deb`, Rowyn has a guarded path for it
(decision D3). Double-clicking the file inspects the package and shows
you exactly what it is — name, version, who published it, its size,
and whether it is signed — then offers the Flatpak if one exists. If
you choose the `.deb`, the Hub installs it into a dedicated **app
container** (a Distrobox named `rowyn-deb`) instead of the system:
the app appears in your application menu, its files stay out of the
signed system image, and it survives Rowyn updates. The first install
downloads the container environment once. You can see every app
installed this way on the Hub's Apps page — marked as app-container
(.deb) apps — and remove them there; updates for them work the
Windows way (download the newer `.deb` and install it over the old
one).

Some `.deb` files are refused, with the reason on screen: packages
that are really part of an operating system — kernel modules and
DKMS drivers, background system services, boot components — cannot
work inside a container. Those belong in the Rowyn system image
itself and arrive with Rowyn updates. Command-line *development*
tools belong in the separate Distrobox the Hub's Development page
sets up.
