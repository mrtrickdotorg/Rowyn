# Updates: small downloads, daily freshness

Rowyn's update goal is that a machine can take the latest packages
and security fixes every day without downloading gigabytes to do it.
Two mechanisms are live today, and a third is built but held back
until its proof passes:

1. **Layered by change frequency** — the image is built as a stack
   of layers grouped by how often their contents change, and an OCI
   client (bootc/ostree) only downloads layers whose digest it does
   not already have.
2. **zstd compression** — layers are published with zstd instead of
   gzip: the whole image is ~300 MB smaller and decompresses much
   faster.
3. **Reproducible builds (in progress)** — CI builds with a pinned
   Buildah, `--source-date-epoch` set to the commit timestamp and
   `--rewrite-timestamp`, and install layers that strip apt lists,
   caches and logs. The goal is that rebuilding an unchanged tree
   produces the *identical* digest, making a no-change day a
   zero-byte day. That proof has **not** passed yet — see
   "Reproducibility status" below — so the daily rebuild cadence
   that depends on it is **not** enabled.

Channels (stable/testing) are covered in
[update-channels.md](update-channels.md); this document is about the
size and cadence of what a client downloads.

## The layer map

Sizes are compressed bytes from the published manifests, measured
2026-11-19. "Before" is the last monolithic build (`a30e8bd`,
2026-11-16, gzip); "after" is the first grouped build (`98a4a04`,
zstd). Layers not listed individually are small config/copy steps.

| Group | Before (gzip) | After (zstd) | Contents |
| --- | ---: | ---: | --- |
| Ubuntu base | 42,719,562 | 36,912,525 | `ubuntu:26.10` base image |
| Builder output (bootc) | 119,207,030 | 104,724,723 | `COPY --from=builder /output` |
| linux-firmware | *(in core)* | 737,722,295 | firmware blobs; huge, changes rarely |
| Core system + kernel | 1,286,029,168 | 455,558,483 | bootc/podman/dracut stack, kernel, drivers |
| Desktop (KDE Plasma) | *(in monolith)* | 1,045,480,702 | ubuntu-minimal/standard, Plasma, KDE apps, VPN clients, Hub runtime libraries |
| PIM / productivity | *(in monolith)* | 126,313,586 | KMail/KOrganizer, printing/scanning, virt-manager/qemu |
| Gaming | *(in monolith)* | 19,897,662 | Mesa/Vulkan helpers, gamemode, corectrl, steam-devices |
| Fonts / media | *(in monolith)* | 46,976,873 | fonts-noto, ffmpeg/gstreamer, orca |
| No-recommends runtime | *(in monolith)* | 2,799,623 | flatpak, distrobox, fuse-overlayfs |
| NVIDIA driver set | *(in monolith)* | 464,950,439 | signed open kernel modules + userland |
| Monolith total (all groups above) | 1,882,992,389 | — | |
| Initramfs | 92,799,740 | 85,324,969 | dracut output for the single kernel |
| Hub binaries + config layers | ~113,000,000 | ~106,000,000 | Hub, helpers, units, branding |
| **Image total** | **3,431,636,725** | **3,134,080,757** | |

The installed package set is unchanged by the split: a
name+version comparison of `dpkg` status between the last
monolithic image and the first grouped image shows the same 2,407
packages, with differences only where the Ubuntu archive itself
moved between the two build dates (a kernel ABI bump 7.3.0-10 →
7.3.0-11 and 19 point updates).

## What a typical update downloads

A client downloads only the layers whose digests it has never
seen. With the grouped layout, a typical day changes at most a
group or two:

- A desktop package security update re-downloads the **desktop**
  layer (~1.05 GB) plus any small config layers — not the whole
  image. (This is the honest worst case among the common ones:
  the desktop group is the largest and the most frequently
  updated. Splitting it further is future work.)
- A kernel update downloads **core** (~456 MB) plus the
  **initramfs** (~85 MB); a firmware update downloads
  **firmware** (~738 MB) — both are infrequent.
- A gaming-stack update is ~20 MB; a PIM update ~126 MB.
- A day on which nothing changed downloads nothing *once
  reproducibility lands*; today it still downloads the changed
  layers (see below).

## Reproducibility status

CI builds with Buildah 1.46.1 (compiled in the workflow from a
pinned Go toolchain — the runner's Buildah 1.33 cannot rewrite
timestamps, and the hosted Go is removed by the disk-cleanup
step), `--source-date-epoch` from the commit timestamp,
`--rewrite-timestamp`, per-step layers, and chroot isolation on
the stock Btrfs storage.

Measured 2026-11-19: two consecutive CI builds of commit
`98a4a04` produced **different** image digests
(`sha256:551aa4e4…` vs `sha256:d4716c5c…`). 38 of 55 layers were
byte-identical; the 17 that differed were all RUN-produced
layers. Layer-level forensics on the small layers named the
causes:

- **Tar entry ordering** in the graph driver's diff output is
  not stable between builds (layers with identical file names,
  contents and metadata still hashed differently — e.g. the
  NVIDIA gate and two config layers). This alone defeats
  byte-identity and cannot be fixed from the Containerfile.
- **Residue files** whose presence varies: dpkg/apt lock files,
  `/etc/.pwd.lock`, apt working directories.
- **Build-machine data embedded in file content**: the
  command-not-found metadata records inode and device numbers
  of the apt lists; `update-alternatives` and dpkg logs embed
  wall-clock timestamps.
- **Whiteout representation** in the rootfs-normalization step
  varies (per-entry whiteouts vs opaque directory whiteouts).

Until the ordering issue is solved (a builder/differ change, not
a payload change), rebuilds of an unchanged tree are *mostly*
identical — the unchanged groups keep their digests and are not
re-downloaded — but the image digest itself is not stable, so
the watcher cannot yet treat a rebuild as a no-op.

## Daily rebuild cadence — not yet enabled

The plan: the Ubuntu digest-pin workflow, on days when the pin
does not move, also dispatches one build, so archive package and
security updates are picked up daily instead of waiting for the
next Canonical base republication. That dispatch ships only when
the double-build proof above passes; enabling it earlier would
push a multi-hundred-megabyte "update" to every client on quiet
days, which is exactly what this work exists to prevent.

## Metered connections

Automatic staging respects metered connections (NetworkManager's
metered flag on the active connection, or the `AllowMetered`
update policy): while metered, the watcher defers and records
`deferred:metered`. Deferral is capped at **72 hours**: the first
metered deferral stamps
`/var/lib/rowyn/update-watcher/metered-deferred-since`, and once
72 hours of continuous deferral have passed, the next automatic
run stages anyway and records the outcome as `staged` with
reason `metered_cap` — the Hub's update card says the download
ran because the deferral limit was reached. Manual checks are
never deferred, and any run that passes the metered gate clears
the stamp, so the 72 hours must be *continuous* deferral.

## zstd:chunked — verdict: unproven, not shipped

Chunked (per-file) compression would let a client fetch only the
changed files inside a changed layer. The `:spike` tag carries
the current build for experiments. As of 2026-11-19 the client
proof has **not** been obtained: converting the spike image
requires pulling it into a local Buildah storage and re-pushing
it as `zstd:chunked`, then pulling it through the ostree client
path while measuring bytes — and the pull side repeatedly failed
from the build VM (egress drops mid-blob; the vfs storage driver
ballooned to ~169 GB unpacking a 55-layer image). No chunked
manifest was ever produced, and no ostree-path measurement
exists. Per the shipping rule — chunked only on a proven client
saving — `:stable` and `:testing` stay plain zstd, which every
OCI client already pulls efficiently at layer granularity. The
experiment remains available whenever a machine with healthy
registry access can run it.
