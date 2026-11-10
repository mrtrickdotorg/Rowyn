# Gaming on Rowyn: graphics stack, 32-bit stance, controller notes

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
the MangoHud and vkBasalt switches. GOverlay and MangoJuice (Apps →
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

## sched_ext schedulers (scx_lavd / scx_bpfland — deferred, spike Nov 2026)

Bazzite's gaming reputation partly rests on sched_ext CPU
schedulers. Rowyn spiked shipping `scx_lavd` and `scx_bpfland` and
is **not** shipping them yet. The findings, in the order they were
verified:

- **The kernel is ready.** The stock Ubuntu 26.10 kernel
  (7.3.0-9-generic) ships `CONFIG_SCHED_CLASS_EXT=y`,
  `CONFIG_BPF_SYSCALL=y` and `CONFIG_DEBUG_INFO_BTF=y` — verified
  from the `.config` inside the archive's own linux-headers package,
  not from release notes.
- **The archive has no userspace.** No `scx*` package exists in the
  stonking archive (checked against the full universe index), so —
  unlike every other Rowyn payload component — the schedulers could
  only arrive as a from-source vendored build.
- **The build could not be completed from this environment.** The
  pinned upstream (sched-ext/scx v1.0.15) was cloned and built with
  clang 18 + libbpf four times; every attempt died in cargo's
  crates.io index/download phase, with registry transfers stalling
  at 0 bytes through this network's egress path while single-file
  fetches succeeded. That is an environment blocker, recorded as
  such — it is not evidence the source fails to build.
- **Two structural blockers would remain even with a clean local
  build.** Rowyn's image build is *offline*: a vendored crate tree
  and a new build stage would be a mechanism no other payload
  component needs. And there is no honest CI smoke for a scheduler —
  a binary-presence check is all CI could assert, below the bar the
  Vulkan and VA-API stacks meet by actually running (`vulkaninfo`,
  ICD/driver file assertions). A scheduler is also boot-adjacent: a
  bad one hangs the machine, so presence-only gating is not enough.

What unblocks it, any one of: an Ubuntu archive scx package (the
clean route — it becomes a payload line like everything else); a
deliberate vendoring project with checksum pinning **plus**
on-hardware validation of both schedulers; or upstream scx
stabilising a release cadence Rowyn can track. If it lands, the
shape is opt-in — a Hub surface reading the kernel's own
`/sys/kernel/scx` state, never a default scheduler — and this
section gets rewritten with the evidence.

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
