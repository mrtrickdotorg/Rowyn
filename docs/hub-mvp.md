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

Steam, Heroic, Lutris and Bottles use the existing typed personal-Flatpak
install/status/cancel job layer. Explicit scans distinguish installed, not
installed and unknown/partial scopes. Opening accepts only the four curated
IDs, checks both scopes and requests the detected scope. It reports a launch
request, not that a detached application window or game is working.

Four environment checks — Steam Play state, Vulkan layers, display HDR/VRR and
controller readiness — are read once when the page opens, bounded and without a
timer, so the "Get set up to play" strip reports this machine instead of
staying empty; each keeps its own button for a fresh read. Nothing is polled.
The Steam section's "Recently played" list is read once on open the same way.
The MVP detects launcher installations with explicit scans. Steam game discovery
is a separate, opt-in read-only scan of bounded `libraryfolders.vdf` and
`appmanifest_*.acf` metadata in common native/Flatpak Steam locations. It
includes external libraries Steam lists but does not read game files, reveal
library paths, launch games, or make compatibility claims. A missing install or
partial metadata read is shown as unknown/partial rather than a clean empty
library. ProtonDB links are generated only from validated numeric app IDs and
lead to community reports; they are not Rowyn test results. Custom Steam roots
outside the locations discovered from Steam's metadata may be missed.

**Recently played** is a separate read of Steam's own per-account local profile
(`userdata/<account>/config/localconfig.vdf`): Steam's `LastPlayed` timestamp
and its playtime counter in minutes, shown as Steam recorded them. Titles are
named only from the install manifests above, so a played title that is not
installed here is counted rather than labelled, and a missing counter reads
"playtime not recorded" instead of zero. There is no frame-rate, latency or
session-length figure, no network request and no timer. Game execution and
per-game compatibility verdicts remain deferred.

Controller rescan calls the same fixed read-only helper. Compatibility copy is
dated 2026-10-31, sourced to Valve's Proton anti-cheat developer documentation
and linked community reports; it is **not Rowyn game validation** or an online
lookup. Links use the Hub's system-browser bridge.

Launch options are per-game copy-only text, not saved config or an execution
bridge. Host wrappers are for an already-installed host-native launcher. They
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
