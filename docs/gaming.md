# Gaming on Rowyn: graphics stack, 32-bit stance, controller notes

## Opening Play

The image installs a **Rowyn Play** application entry alongside **Rowyn Hub**.
It runs `/usr/bin/rowyn-hub-shell --page Play`, so the app menu opens the Hub
directly on its Play page — launchers, the Steam library, compatibility and
controller checks — instead of the dashboard. Both entries use the same
binary and window class; the Hub entry still opens the dashboard.

## Vulkan on the host

The host Vulkan stack is *named in the image*, not inherited by package
closure: the payload installs `mesa-vulkan-drivers` and `vulkan-tools`,
and the CI image smoke requires the lavapipe and Radeon ICD manifests to
be present and `vulkaninfo --summary` to enumerate a device inside the
built image. AMD and Intel GPUs use Mesa's RADV/ANV drivers from that
package; NVIDIA GPUs use the signed NVIDIA driver stack (see
`shared/gpu-select.sh` for how the image picks nvidia vs nouveau per
boot). This host stack is what native games, emulators run outside
Flatpak, and tools like `vulkaninfo` see.

## VA-API video acceleration

The VA-API stack is named in the image the same way: `mesa-va-drivers`
(the Gallium VA drivers in `mesa-libgallium` — radeonsi, r600,
nouveau, virtio-gpu), `intel-media-va-driver` (the iHD driver for
Intel Gen8+), `i965-va-driver` (older Intel) and `vainfo` for
diagnosis, all asserted by the CI image smoke. Before this was named,
no VA driver shipped at all, so hardware video decode/encode silently
fell back to software in the places that use VA-API: browser video,
OBS recording/encoding, and Steam Remote Play / Sunshine capture
pipelines. NVIDIA uses its own stack (NVENC/NVDEC through the signed
driver), not VA-API.

## The 32-bit (i386) stance

Rowyn does **not** enable i386 multiarch on the host, and installs no
32-bit host libraries. This is deliberate:

- The supported gaming route is Flatpak (Steam, Lutris, Heroic,
  Bottles from the Hub's Play page and Apps catalog). Flathub's
  runtimes carry their own 32-bit compatibility layers
  (`org.freedesktop.Platform.Compat.i386` and the GL32 extensions), so
  32-bit Windows games under Proton/Wine work without any host
  multiarch.
- Enabling i386 on the host would only serve native `.deb` installs,
  which Rowyn's application policy already routes to Flatpak/Distrobox
  instead (see `shared/installed-apps.sh`). A second, half-tested
  32-bit stack on the host is not worth its failure modes.

If a native Linux game binary refuses to start with a missing 32-bit
loader error, the answer is to run it through the Flatpak launchers or
a Distrobox container, not to multiarch the host.

## MangoHud, gamescope and vkBasalt inside Flatpak

Overlays and the gamescope compositor reach Flatpak games as Vulkan
layer extensions of the Freedesktop runtime, installed per-user from
Flathub:

- `org.freedesktop.Platform.VulkanLayer.MangoHud` — the FPS/perf overlay.
- `org.freedesktop.Platform.VulkanLayer.gamescope` — the gamescope
  layer for launchers that drive it (Lutris, Bottles).
- `org.freedesktop.Platform.VulkanLayer.vkBasalt` — post-processing
  (CAS sharpening, colour effects), enabled per game with
  `ENABLE_VKBASALT=1`.

The Play page checks which of these are installed for the runtime
branch Steam actually uses, installs the missing ones, and its
launch-options builder — including the per-game profiles — composes
the MangoHud and vkBasalt switches. It also reads back the launch options
Steam has recorded for each title and says whether the saved profile's command
is what Steam will run, so a profile that was never pasted in shows as drift
rather than as applied.

MangoHud's settings live inside the Flathub sandbox at
`~/.var/app/com.valvesoftware.Steam/config/MangoHud/MangoHud.conf`, and a
host-native MangoHud reads `$XDG_CONFIG_HOME/MangoHud/MangoHud.conf` when that
variable is set to an absolute path (the `~/.config` path otherwise). Neither path
is a fallback for the other: a Flatpak game never reads the host file, and a
host-native game never reads the sandbox one. The Play page follows the
launcher boundary selected above — sandbox path for Flatpak Steam, host path
for host-native — reports whether that config exists and how many settings it
holds (and names the other boundary's file), and offers a small starting
config to copy. It never writes either file; `MANGOHUD=1` in the launch
options is still what turns the overlay on.

## Opt-in helpers: `rowyn-tune`, `rowyn-game-launch`, `rowyn-mimalloc-run`

The image installs three small gaming helpers. The Play page has a **Rowyn
gaming helpers** block that reports whether they are present and what
`rowyn-tune` is set to, with an enable/disable button and copyable examples:

- `rowyn-game-launch` (`/usr/bin/rowyn-game-launch`) runs a command in the
  `rowyn-gaming.slice` user slice, through `gamemoderun` when GameMode is
  installed, and falls back to a plain run if the user systemd manager cannot
  be reached. In Steam it is a launch option: `rowyn-game-launch %command%`.
- `rowyn-mimalloc-run` (`/usr/bin/rowyn-mimalloc-run`) preloads the mimalloc
  allocator for one command only (`MIMALLOC_LARGE_OS_PAGES=1`); it is never
  preloaded globally, and 32-bit games may log a harmless preload warning.
  Steam launch option: `rowyn-mimalloc-run %command%`.
- `rowyn-tune` (`/usr/bin/rowyn-tune`) switches Rowyn's opt-in latency
  profile with `rowyn-tune status|on|off`. `on` writes
  `/etc/sysctl.d/90-rowyn-gaming.conf` (smaller dirty-page limits, no
  proactive compaction, busy polling) and applies it now and at boot; `off`
  removes the drop-in, and the running kernel keeps the tuned values until
  the next boot. It trades sustained disk-write throughput for latency.

Both wrappers are host-side files, so they are not reachable from inside
Flatpak Steam; the examples are for a host-native launcher only. The Play
button switches `rowyn-tune` through the same `sudo -A` askpass helper the
VPN and Sunshine firewall actions use, and only ever runs the fixed helper
with `on` or `off`.

## GameMode for Flatpak Steam games

GameMode is installed in the image, and `/etc/gamemode.ini` hooks Rowyn's
low-latency PipeWire quantum (256 samples) and the `performance` power
profile onto every GameMode session. Whether a *sandboxed* Steam game can ask
for one depends on the client inside Steam's sandbox:
`gamemoderun %command%` runs `/usr`- and `/app`-side, and the host's
`/usr/games/gamemoderun` is not reachable from there.

What is verified is the client's presence, by reading the deployment: the
`org.freedesktop.Platform` runtime Steam uses (26.08) ships `gamemoderun` and
its `libgamemode*` libraries in its own files, which mount at `/usr` inside
the sandbox — Steam 1.0.0.87 on the pinned Flathub build, checked 2026-11-24;
the app's own files ship neither. **That is the whole of the verification. No
game has been launched under `gamemoderun %command%` on the Rowyn image yet,
so activation, the `gamemode.ini` hooks and their effect are untested rather
than established**, and the page's copy says exactly that.

A sandbox client is not enough for every title, which is separately clear
from the loader path. Steam starts Windows titles through Proton or the Steam
Linux Runtime, and those run in a pressure-vessel container with its own
`/usr`. pressure-vessel captures the `LD_PRELOAD` entry, but the client it
loads then `dlopen`s `libgamemode.so.0`, which the installed Steam runtime
tree does not carry and which pressure-vessel will not take from the host
(`/usr` is reserved by the container framework). That is the failure upstream
reports as ValveSoftware/steam-runtime#814 (mirrored as
ValveSoftware/Proton#9645), and the reason the Play page gates the option for
those titles instead of offering it. The host's `/usr/games/gamemoderun` is
likewise unreachable from the sandbox.

Detection follows deployments, not version strings. The Play page reads the
runtime the app's metadata declares and checks it and the app's files for the
client, and it reads Steam's own `CompatToolMapping` (the title's entry, else
the `"0"` default) to know whether a title runs in a container. The per-game
GameMode option is offered only when the sandbox client exists, the host has
`gamemoded`, and the title is not a container launch; a Proton or
Steam-Linux-Runtime title is told why and pointed at launcher-wide GameMode.
Nothing is assumed: a build, runtime or title whose pieces are missing keeps
the option hidden.

For the titles that do get it, the option says what it can and cannot claim.
GameMode's per-process registration is not expected to cross the Flatpak PID
namespace, so `gamemoded -s <pid>` would not list the game and `renice`/CPU
pinning would not reach it, with MangoHud's GameMode chip reading off for the
same reason — none of which has been observed on the image either. The
`/etc/gamemode.ini` hooks (the 256-sample PipeWire quantum and the
`performance` power profile) are configured for every GameMode session; if a
sandboxed game starts one, they are what would run. The daemon is
D-Bus-activated, so an installed-but-inactive `gamemoded` is normal and is not
reported as broken.

**Start launchers under GameMode** is the route for container titles: the
Play page starts `flatpak run …` through `/usr/games/gamemoderun` on the
host, which opens a GameMode session for the launcher's lifetime. It is opt-in
and per session for a reason — the configured power profile and raised clocks
last until the launcher exits, including time spent in the store or the
friends list. This path is not verified end-to-end on the Rowyn image either.

gamescope's per-game option is host-only, and honest about it: it
prefixes a native command with the `gamescope` binary, so the Play page
offers it only on the host boundary and only when `gamescope` resolves
in the session's PATH. On the Flatpak boundary the option is not
offered at all — the Vulkan layer above is what reaches a sandboxed
launcher that drives gamescope itself, and a layer cannot start a
compositor. GOverlay and MangoJuice (Apps →
Gaming) edit the overlays' settings graphically, and LACT, OpenRGB,
Piper and Solaar live in the Tuning & Peripherals pack. Native
(host) games can use the distribution's tools inside Distrobox.

## VR, and why Steam is a Flatpak here

Steam ships as a Flatpak on Rowyn (preinstalled). Wireless VR works
through **WiVRn** (Apps → Gaming), a Monado-based OpenXR streamer
for standalone headsets; its caveat — tethered SteamVR's setcap'd
compositor does not survive the sandbox — is stated in its catalog
entry. The full comparison behind the Flatpak choice (update
mechanics under bootc, VR, Remote Play, anti-cheat) is in
`docs/pass2-native-steam-evaluation.md`; the short version is that
native Steam would require host i386 multiarch the image
deliberately does not carry, and Distrobox remains the advanced
route for the cases native wins.

## sched_ext schedulers (scx_lavd / scx_bpfland — closed, no-go for 26.10)

Bazzite's gaming reputation partly rests on sched_ext CPU schedulers, so
Rowyn reviewed the question against the Ubuntu 26.10 base and closed it on
**2026-11-24 as a no-go for this cycle**: the kernel is ready, the archive
still is not, and neither bridge across that gap is worth its cost yet.

**Kernel: ready.** The archive's `linux-image-generic` is `7.3.0-9.9`, built
from Ubuntu's stonking kernel annotations, which set
`CONFIG_SCHED_CLASS_EXT=y`, `CONFIG_BPF_SYSCALL=y`,
`CONFIG_DEBUG_INFO_BTF=y` and `CONFIG_BPF_JIT_ALWAYS_ON=y` for amd64, with
`CONFIG_BPF_UNPRIV_DEFAULT_OFF=y` — so loading a scheduler needs root, which
is the intent anyway. Upstream's `kernel.config` is its own *development*
config: where it differs from Ubuntu (`PROVE_LOCKING`, `DEBUG_ATOMIC_SLEEP`,
`PREEMPT`), the switches it asks for are debug options for scx's selftests,
not runtime requirements.

**Archive: still no package.** Checked on 2026-11-24 against the stonking,
resolute and questing source and `binary-amd64` indexes: the only `scx`
match is `qt6-scxml`, Qt's state-chart library. Launchpad's
published-sources lookup returns zero entries for both `scx` and
`scx-scheds`, and Debian's source index has none either. Upstream
v1.1.4's own `INSTALL.md` Ubuntu section still says "build from source"
(clang, llvm, libelf-dev, libbpf-dev, pahole, protobuf-compiler,
libseccomp-dev, cargo), which is a distribution gap, not a Rowyn choice.

**Upstream: trackable, not consumable.** scx is at v1.1.4 (2026-10-15) with
roughly six releases in 2026, a mostly two-month cadence and a *stable* Rust
toolchain — but releases are source-only: no `.deb`, `.rpm` or prebuilt
binaries, so every bump means a build on Rowyn's side.

**Build blockers.** Rowyn's image build is fully offline (Containerfile;
build assets are fetched on the host first). The vendoring precedent exists
— bootc ships as a host-fetched source tree with vendored crates and
`CARGO_NET_OFFLINE=true` — but scx would add a second vendored tree that
needs clang/llvm, libbpf and bpftool on top of cargo, a build pin to bump
every couple of months, and a maintenance surface (BPF programs, kernel
version coupling) no other payload component has.

**Validation blockers.** CI could assert only that the binaries and units
exist — below the bar the Vulkan and VA-API stacks meet by actually running.
A scheduler is also boot-adjacent: a bad load hangs or wedges the machine,
so presence-only gating is not acceptable. Closing that gap needs
on-hardware runs of both schedulers on the signed, Secure Boot-enrolled
image, a recovery path (boot the previous deployment) and a documented
revert to the kernel default. This environment cannot produce those runs,
and no frame-rate or latency claim can be made from here.

What unblocks it, any one of: an Ubuntu archive scx package (the clean
route — a payload line, a presence plus `/sys/kernel/sched_ext/state` smoke,
and the same on-hardware validation); a deliberate vendoring project with
checksum pinning **plus** that validation; or upstream publishing distro
packages it already tracks. If it lands the shape stays opt-in: a Hub
surface that reads `/sys/kernel/sched_ext/state` and `root/ops` and starts
one scheduler on request — never a default scheduler, never a bundled
benchmark. Today no scx surface ships: the port removes upstream's
`commands::gaming` registrations, including `scx_status`, `scx_available`
and `scx_set_scheduler`.

## Xbox controllers over Bluetooth (xpadneo — deferred)

`xpadneo` (the advanced Xbox One/Series Bluetooth pad driver) is a
DKMS out-of-tree kernel module. Rowyn's kernel modules must be built
and signed at image-build time — the NVIDIA open modules ship exactly
that way, as Canonical-signed prebuilt packages — and there is no
equivalent signed, maintained xpadneo package for the Ubuntu kernel
Rowyn pins. Building and self-signing a DKMS module inside the image
would add a private signing key to the build and a module whose
maintenance burden lands on every kernel bump.

Deferred, therefore, until one of these changes: Ubuntu ships a signed
xpadneo (or it lands in the kernel), or the module-signing path used
for a future Rowyn-owned module makes the cost reasonable. In the
meantime: Xbox pads work wired (USB) and over Bluetooth with the
kernel's in-tree `xpad`/`hid-microsoft` drivers for basic input;
`rowyn-controller-check` reports what it finds. PlayStation and Switch
pads are covered by the in-tree drivers and `steam-devices` rules.
