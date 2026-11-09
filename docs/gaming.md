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

## MangoHud and gamescope inside Flatpak

Overlays and the gamescope compositor reach Flatpak games as Vulkan
layer extensions of the Freedesktop runtime, installed per-user from
Flathub:

- `org.freedesktop.Platform.VulkanLayer.MangoHud` — the FPS/perf overlay.
- `org.freedesktop.Platform.VulkanLayer.gamescope` — the gamescope
  layer for launchers that drive it (Lutris, Bottles).

The Play page's launch-options helper can add MangoHud to a game's
launch options; see the Play page guidance for the exact steps. Native
(host) games can use a distribution MangoHud inside Distrobox.

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
