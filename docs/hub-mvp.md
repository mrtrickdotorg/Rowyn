# Hub Diagnostics and Play MVP

Rowyn owns the small source overlays in `build/hub/`. The pin-export port
applies them **after** the legacy cuts; the 58-module shared closure, removed
helper binaries, gaming backend, just runner and root-daemon client stay cut.
Apps, Updates and VPN keep their existing backends.

## Diagnostics (`/diagnostics`)

Checks run only after **Refresh checks**; no mount-time scan or recurring poll.
Native reads inspect bounded OS/kernel/memory/module files and fixed absolute
`bootc status --json`, `lspci -nnk`, `systemctl --failed`, `df` and
`rowyn-controller-check --json` commands. Commands run as the signed-in user,
with an eight-second bound each and capped capture. A denied bootc read is
**unavailable**, not an invitation to elevate privileges. No repair, reboot,
firmware, package installation, journal upload or recovery actions exist here.

The bridge scrubs credentials, host/user/network identifiers and ANSI escapes
before display; the controller projection omits raw devices and mutation
advice. Scrubbing is best effort: the report preview explicitly asks the user
to review before sharing. Copy uses that preview; **Save report to Downloads**
creates a new `0600` text file only on request, refuses overwrite, and accepts
no caller-selected filesystem path. No files are created by collecting checks.

## Play (`/play`)

The page has its own app-menu entry, **Rowyn Play**
(`com.rowyn.play.desktop`), which starts `/usr/bin/rowyn-hub-shell --page Play`
so the launchers, Steam library and setup strip open directly instead of
landing on the dashboard first. The general **Rowyn Hub** entry is unchanged
and still opens the dashboard; both share the same window class.

Steam, Heroic, Lutris and Bottles use the existing typed personal-Flatpak
install/status/cancel job layer. Explicit scans distinguish installed, not
installed and unknown/partial scopes. Opening accepts only the four curated
IDs, checks both scopes and requests the detected scope. It reports a launch
request, not that a detached application window or game is working.

The environment checks — Steam Play state, Vulkan layers, display HDR/VRR and
controller readiness — are read once when the page opens, bounded and without a
timer, so the "Get set up to play" strip reports this machine instead of
staying empty; each keeps its own button for a fresh read. Nothing is polled.
The same pass reads Steam's shader pre-caching flag, the recently played list
and the MangoHud config once, and the strip projects all three alongside the
environment steps — shader pre-caching as on/off/not-recorded, recent play as
what Steam records, and MangoHud as the config file (or its absence, which is
a starting point, not a fault). Each of those sections keeps its own button
for a fresh read.
The MVP detects launcher installations with explicit scans. Steam game discovery
is a separate, opt-in read-only scan of bounded `libraryfolders.vdf` and
`appmanifest_*.acf` metadata. Each native and Flatpak Steam install is located
at its common path, and every library folder that install declares — a second
drive, an external SSD, an SD card, including the older plain-value form of the
list — is scanned in turn, so a title is not left out because it lives
elsewhere. It reads no game files, reveals no library paths, launches no game
and makes no compatibility claim. The scan reports how many folders it read and
how many that Steam lists could not be read (an unmounted drive, a card that is
not inserted, a path this user cannot open), so those titles are stated as
missing rather than implied by a generic warning; an unparsed folder list is
reported as such. A missing install or partial metadata read is shown as
unknown/partial rather than a clean empty library. ProtonDB links are generated
only from validated numeric app IDs and lead to community reports; they are not
Rowyn test results. Steam roots that no manifest or configuration metadata
points at remain outside this scan.

**The library comes first.** Detected titles are sorted for reading and shown
with Steam's own cached cover art from `appcache/librarycache` — the 600×900
capsule where it exists, else the header or capsule — embedded as a bounded
data URL (768 KB per file, 8 MB per scan). A title with no cached artwork gets
a letter tile, and the page states how many titles actually had a cover; no
artwork is downloaded and no game files are read. The list can be searched by
title (accent- and punctuation-tolerant) or app ID and filtered by curated
state or "no curated note", all locally. One line reports coverage — installed
titles, how many carry a curated note, how many have cached art — and only the
first 60 covers in the current result set are rendered, so a large library does
not put every base64 cover in the document; the rest keep their letter tile and
the line says so, and a search or filter gives them their cover back. Each title shows a one-word
curated state (Native, Proton, Tweaks or Blocked) only when the shipped curated
entry carries that title's Steam app ID — never by title alone, so a remake,
demo or re-release cannot borrow a same-named game's note. A title with no
entry, or an entry the dataset records by name only, shows no verdict. Its
profile editor, provenance and community links live inside a single closed
"Launch options" disclosure instead of expanding on the page.

**Recently played** is a separate read of Steam's own per-account local profile
(`userdata/<account>/config/localconfig.vdf`): Steam's `LastPlayed` timestamp
and its playtime counter in minutes, shown as Steam recorded them. Titles are
named only from the install manifests above, so a played title that is not
installed here is counted rather than labelled, and a missing counter reads
"playtime not recorded" instead of zero. There is no frame-rate, latency or
session-length figure, no network request and no timer. Game execution and
per-game compatibility verdicts remain deferred.

**Shader pre-caching** is a read of Steam's global `config/config.vdf` — the
same file the Steam Play check parses — for the
`ShaderCacheManager → DisableShaderCache` flag behind Steam's own
Settings → Downloads → Shader Pre-Caching toggle. It is shown as Steam recorded
it; an absent flag reads "not recorded" (Steam's default applies) rather than
being guessed, and the read never edits the file or starts Steam. One line of
guidance names pre-caching as the usual fix for stutter in a game's first
minutes, while shaders compile.

**MangoHud settings** follows the launcher boundary selected above. Flathub
Steam reads `~/.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf`;
a host-native MangoHud reads `$XDG_CONFIG_HOME/MangoHud/MangoHud.conf` when that
variable is an absolute path (else `~/.config/MangoHud/MangoHud.conf`), shown as
`~/…` when under home and absolutely otherwise. The read
reports both paths and the state of the one in use — exists, readable, how many
settings — and names the other boundary's file so a guide-follower can see why
their settings do nothing, rather than treating either file as a fallback. The
read never creates, edits or backs up
the file and starts nothing; a small recommended starting config is offered
as copyable text only, and `MANGOHUD=1 %command%` remains the switch that
turns the overlay on.

Controller rescan calls the same fixed read-only helper, but the section now
opens with four player lines — a controller-like device is present, its
kernel driver is reported, this session can write `/dev/uinput` for Steam
Input and remappers, and Flatpak Steam's own sandbox is granted the input
devices — with the helper's technical rows folded into a details toggle. The
last line comes from `flatpak info --show-permissions com.valvesoftware.Steam`
(`[Context] devices=`, `all` or `input` covering `/dev/input`) plus a
read-only check that this session can open a `/dev/input/event*` node; no
permission is changed. Because a pad this session can open is not a pad a
sandboxed launcher can reach, a sandbox without the devices turns the pad line
into a warning and the setup step into a task, and a permission read that could
not run stays unknown instead of assuming access. Advice is a fixed per-family table in Rust (PlayStation, Xbox, Switch,
8BitDo, the Microsoft wireless adapter) built only from the families the scan
saw — from the helper's USB rows or from the name of a controller-like input
device, so a Bluetooth pad gets the same advice as a wired one — and emitted in
the table's order. The helper's own hint text, device list and USB ids are
still dropped, and the table names no command or file to change. It is a readiness read, not
input testing: no button, stick, trigger or rumble test runs.

Compatibility copy is dated once per section rather than once per title. The
Compatibility section carries a single provenance note (reviewed 2026-10-31,
sourced to Valve's Proton anti-cheat developer documentation and linked
community reports; it is **not Rowyn game validation** or an online lookup),
and the Steam library section states the curated dataset's own date and size
once — last checked 2026-11-24, counting only the entries whose Steam app ID
is known. The dataset is Rowyn's own copy in `build/hub/src/data/`, re-checked
against ProtonDB, Are We Anti-Cheat Yet, the Steam store and any publisher
statement that settles a title, and every entry names the date it was checked. Once that date is more than six months old the same note says so in
place ("more than six months ago — treat them as stale and check ProtonDB") and
the frontend tests print a FLAG naming the age; the check fails if stale data
is ever presented as merely dated. A verdict appears only on
a title that has one; no card repeats a date or a "not tested" label. The
dataset is re-checked before any release that ships a change to those notes,
and its date is shown in the library section rather than repeated on each
card. Links use the Hub's system-browser bridge.

Launch options are per-game copy-only text, not saved config or an execution
bridge: the page never writes Steam's configuration. It does read back what
Steam has recorded for a title — `userdata/<account>/config/localconfig.vdf` →
`apps/<appid>/LaunchOptions`, from the account Steam used last — and compares it
with the command it would build, so every card says **In sync**, **Drift**
(naming Steam's own text), **Not applied**, or **Not checked** when the profile
could not be read. A saved profile is a choice the player still has to paste in,
never evidence that it is in effect. Host wrappers are for an already-installed
host-native launcher. They
are **not generally accessible inside Flatpak Steam**; the sandbox default is
`%command%`. The page neither installs a native launcher nor recommends a
sandbox escape. Mimalloc is experimental and opt-in per game; no custom
kernels, sched-ext, telemetry, Guardian, generic argv or privilege bridge is
introduced.

## Work (`/work`)

Work is its own destination directly after Play, so printing, office documents
and focus time no longer sit inside Apps (which keeps the App Store). Printing
is the only part with a native bridge, and it is entirely fixed-argument
`lpstat`/`lp`/`lpadmin` work in `commands/rowyn_work.rs`: read queues (state and
default reported by CUPS, unknown when CUPS does not answer), send the image's
`/usr/share/rowyn/print-test-page.txt` to one named queue, and create one new
queue from a discovered IPP address.

Queue creation is the only mutating action. The page scans with `ippfind` on
demand, splits the answer into IPP addresses this page can add, names that are
already queues, and anything else, and shows an in-page confirmation for the
chosen address before anything runs. The bridge accepts only `ipp://` and
`ipps://` addresses, refuses a name that already exists (because `lpadmin -p`
would rewrite that queue), fails closed when the queue list cannot be read, and
adds with the driverless `everywhere` model — no vendor driver. Adding a printer
is an administrator action: when CUPS refuses, the reported reason is CUPS's own
text plus a pointer to the system authorization prompt in Print settings, and
the page re-reads the queues afterwards so it shows what exists rather than what
was requested. Removing or reconfiguring a queue stays in Print settings.

Office and mail entries are the same typed personal-Flatpak jobs as the App
Store, and the Microsoft 365 entries open as web apps in their own tab. Focus
sessions call the existing bounded session bridge; the page starts and ends one
on request and never runs on a timer.

## Section anchors, boot health, and clipboard

Each Rowyn page follows its declared manifest sections itself: `useSectionFollow`
maps `?section=` (hash query or search string) to a rendered `hub-section-*`
anchor, so a search result or section link lands on the section instead of the
top of the page. `build/tests/test_hub_overlay_contract.py` fails when a
declared section has no anchor, a page stops following its sections, or a page
starts polling.

Updates and Home read `/var/lib/rowyn/boot-health.json` through a bounded,
root-owned, symlink-refusing native command and a strict frontend model: known
good count, per-digest failures, quarantine reasons, and rollback attempts. The
panel reads the record. Clearing one quarantine is the only write, and it asks
for confirmation first: the Hub runs the same fixed-argument `clear-quarantine -`
helper the terminal uses, with the digest on stdin, after re-checking that the
digest is still quarantined in the record it reads itself. Clearing only allows
that image to be staged again; nothing is downloaded, installed or restarted.

Copy actions try the webview clipboard, fall back to a selection copy, and
report a typed failure that points at the native report download instead of
claiming success.

## Tests

`ROWYN_TEST_UPSTREAM_REPO=/path/to/pinned/checkout ./build/validate.sh` covers
source and actual export contracts. Validation needs `shellcheck`, `skopeo`,
and `actionlint` as well as Python 3 with PyYAML, and stops before the suite
when any is missing. CI installs pinned releases of both linters (ShellCheck
0.11.0 and actionlint 1.7.12) so inline `run:` findings do not follow the
runner image; a local run uses whatever version is installed. In a fresh exported Hub run
`npm ci && npm run build && node --test tests/*.test.mjs`, then in `src-tauri`
`cargo test --locked`. The `Hub source tests` CI job runs that export, the
Rowyn suite (`node --test tests/rowyn-*.test.mjs`) and the Rowyn Rust tests
(`cargo test --locked rowyn_ -- --nocapture`) on every push, so a section,
wiring, model or fixture-helper regression fails the build instead of
shipping. The job fails when one of the twelve policy-fixture tests reports
that it skipped, so a green run means they executed. The Rowyn MVP tests execute real TS model functions,
SSR page construction, native parsers, launcher allowlists, bounded scrubbing,
single-flight checks and private explicit report export. No VM gate is added.
Live launcher downloads, desktop windows, clipboard and physical controllers
still require an installed-system check; container unit tests do not certify
that hardware or desktop integration.
