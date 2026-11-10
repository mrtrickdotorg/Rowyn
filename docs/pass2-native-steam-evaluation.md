# Spike: native Steam vs Flatpak Steam on Rowyn (Pass 2, Nov 2026)

**Recommendation: no architecture change.** Steam stays a system
Flatpak (preinstalled since Pass 2 Batch B). Native Steam remains
available to advanced users through Distrobox, which is Rowyn's
documented escape hatch for native packages. The evidence, axis by
axis:

## Update mechanics under bootc (decisive)

Rowyn's OS is an atomic bootc image; applications are Flatpaks
precisely so app updates never require an OS rebuild
(`shared/installed-apps.sh`). Flatpak Steam updates through the
normal Flatpak path (Hub/Discover, system installation) on its own
cadence, and Steam's own client self-update inside the sandbox works
as Valve intends.

Native Steam is a `.deb` — which Rowyn's application policy routes
away from the host on purpose — and its runtime needs a 32-bit host
library stack. Rowyn deliberately does **not** enable i386 multiarch
on the host (docs/gaming.md, "The 32-bit stance"): Flathub's
runtimes carry the 32-bit layers instead. Native Steam on the host
would mean either breaking that stance (a second, half-tested 32-bit
stack baked into the signed image) or accepting that Steam's
self-updater writes into a system the image model says is
read-only. Neither is a trade worth making for the default path.

## VR

This is Flatpak Steam's weakest axis, honestly stated:

- Tethered SteamVR wants a setcap'd `vrcompositor` and direct
  device access; the sandbox blocks the setcap path, so tethered
  SteamVR under Flatpak Steam is fiddly and partly unsupported.
- The wireless route is in better shape: **WiVRn** (offered in the
  Apps → Gaming pack) is a Monado-based OpenXR streamer for
  standalone headsets that works with Flatpak Steam for OpenXR
  titles. Its catalog copy carries the caveat rather than hiding it.

Native Steam improves tethered SteamVR specifically. Users who need
it are exactly the advanced case Distrobox covers (native Steam in a
container with GPU access), without changing the default for
everyone else.

## Steam Link / Remote Play

No material difference. Remote Play's client and host paths work
from the Flatpak (network + device permissions are granted by the
manifest), and Rowyn additionally ships a first-class host path —
Sunshine with a Hub setup flow (Play page) — plus the Moonlight
client in the catalog. The VA-API stack named in Batch A is what
Remote Play encoding needed, and it benefits both packagings
equally.

## Anti-cheat edges

Anti-cheat outcomes are decided by the game and its middleware
enabling Proton support (EAC/BattlEye opt-in) and by kernel-level
components that refuse Linux entirely — both identical under either
packaging, because both run the same Proton builds against the same
host kernel. Packaging is not the variable; the curated verdicts on
the Play page (checked against ProtonDB reporting) remain the honest
answer per title.

## What would change this recommendation

- Valve shipping and supporting a native package that does not
  require host i386 multiarch;
- tethered SteamVR becoming a mainstream (not enthusiast) use case
  for Rowyn's audience, with the Flatpak path still blocked;
- Flathub's Steam maintenance lapsing.

Until one of those is true: Flatpak by default, Distrobox for the
exceptions, WiVRn for wireless VR.
