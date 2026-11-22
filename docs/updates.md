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
3. **Reproducible builds (proven 2026-11-21)** — CI builds with a
   pinned Buildah, `--source-date-epoch` set to a fixed project
   epoch and `--rewrite-timestamp`, and install layers that
   strip apt lists, caches and logs. Rebuilding an unchanged tree
   now produces the *identical* digest — see "Reproducibility
   status" below — so a no-change day is a zero-byte day, and
   every rebuild is compared with the digest recorded for the
   same source revision and package set, so the claim cannot
   regress silently. The daily rebuild cadence that depends on
   this proof was enabled on 2026-11-21 — see "Daily rebuild
   cadence" below.

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
- A day on which nothing changed downloads nothing: the rebuild
  produces the identical digest, so the watcher treats it as a
  no-op (see "Reproducibility status" below).

## Reproducibility status

**Proven 2026-11-21.** Two consecutive CI builds of commit
`1503da0` (runs 44862598017 and 44866139988) produced the
**identical** image digest
`sha256:2e25ca15781b201e07bb899d2e26c3cc019e4280f46f561facc3421728013b8c`,
with all 56 layers byte-identical and the two manifests
byte-identical. The November 19 measurement that opened this
work — two builds of `98a4a04` differing in 17 of 55 layers —
is closed out; every cause found was fixed at its source:

- **Storage-driver variance (the Nov 19 lead blocker).** The two
  forensic builds had silently run different graph drivers
  (btrfs vs overlay), whose differs disagree on entry ordering,
  hardlink emission and whiteout form. The provisioning action
  now pins `driver = "btrfs"` in storage.conf before the store
  is first opened, and the pinned compressed-storage setup has
  used it since.
- **Differ ordering inside the payload.** The builder stage's
  `/output` tree is packed as a name-sorted, mtime-clamped tar
  and extracted in a RUN step, so its shipped layer has a single
  producer with a stable entry order.
- **Install-step residue and build-host data.** Every install
  tail now removes dpkg/apt locks, `/etc/.pwd.lock`,
  `/run/adduser`, dracut's `/run/tmpkbd.*` temp dirs and the
  blkid cache (which recorded the build runner's own loop
  device and filesystem UUID); the command-not-found metadata's
  recorded inode/device numbers are zeroed; the plymouth step's
  alternatives log is truncated; the build-time plocate database
  is dropped (the on-device timer rebuilds it); the rootfs
  normalization empties `/boot` and `/var` in place so whiteout
  form is deterministic; the NVIDIA step mounts a tmpfs over
  `/boot` so its trigger-regenerated initramfs never enters the
  layer.
- **Per-machine identity material.** `/etc/machine-id` is
  emptied, SSH host keys and the build runner's resolv.conf stub
  are removed (sshd-keygen and systemd generate them per
  machine), the iSCSI initiator name is normalized, and the
  ssl-cert snakeoil pair is deleted — `ssl-cert.service`
  regenerates it on first boot.
- **Compiled-binary variance.** rustc emits DWARF location lists
  in parallel-codegen completion order, so the bootc binaries'
  debug sections differed between builds even when every code
  section was byte-identical, and the GNU build-id — hashed
  over the unstripped output — preserved that variance after
  stripping. The builder stage now strips the four binaries and
  removes the build-id note; the image also got ~94 MB smaller.
- **History mount checksums.** Buildah appends each bind
  mount's stage image digest to the RUN step's history
  `created_by`, and stage digests are themselves not
  reproducible (copier/differ walk order inside the stage
  builds) — with all layers matching, those suffixes were the
  only differing bytes left in the config. A "Normalize image
  history" step in the Build job round-trips the image through
  an OCI layout and strips the `:sha256:…` suffixes (commands
  stay) before the publish pushes run. Verified offline first:
  stripping the suffixes made two real builds' configs
  byte-identical.

The installed package set is unchanged by all of this: the
final build's name+version manifest is the same 2,407 packages
as the November 19 grouped image, with zero differences.

### Cross-commit stability

The proof above is same-commit: two builds of one tree. The
first daily rebuild exposed the gap — it built a tree one
docs-and-workflow commit ahead of the morning's build and
differed in 50 of 56 layers at byte-identical sizes. The config
diff isolated the only two independent variables, both scoped
to HEAD rather than to the image's inputs:

- the build epoch was the HEAD commit's timestamp, and
  `--rewrite-timestamp` clamps every file mtime to it, so each
  new commit re-stamped every layer;
- the source-revision stamp was the HEAD commit, carried in the
  `org.opencontainers.image.revision` label and in
  `/usr/share/rowyn/build-info.json`.

Both are now input-scoped, in `.github/workflows/build.yml`:

- `ROWYN_SOURCE_DATE_EPOCH` is a fixed project constant — the
  timestamp of the repository's first commit (2026-10-25) —
  used for the Buildah build, the ctx mtime pin and the
  promoted derivative's `--timestamp`. It never moves; moving
  it once would re-stamp every layer for every client.
- `ROWYN_IMAGE_INPUT_PATHSPEC` lists every path whose content
  can change the image's bytes (the Containerfile, `shared/`,
  `keys/`, `docs/assets/`, the Hub generator and its overlays,
  the asset fetcher with its in-tree pins, the derivative
  inputs, the build workflow itself and the storage
  provisioning action; generated trees are covered through
  those committed provenances). The stamp is the last commit
  that touched the pathspec, derived in the Build job from a
  full-history checkout. `build/tests/test_image_inputs.py`
  asserts every Containerfile COPY source is covered, so a
  future input cannot silently escape the stamp.

Semantics: a commit that changes no image input — docs, tests,
unrelated workflows — produces the *identical* image digest,
layers, config and manifest. A commit that changes one input
moves only the layer(s) built from it, plus the small
build-info layer whose stamp legitimately changed. Archive
changes behave as before: only the affected group layers move.

**Proven 2026-11-21.** Three CI builds exercised the three
cases back to back:

- The fix tree (6388718) built twice — push run 44941428346
  and dispatch run 44946272351 — produced the identical base
  digest `sha256:ec1a7dca…` and the identical `:stable`
  derivative `sha256:307deca5…`.
- A whitespace-only README commit on top (a93bed0, dispatch
  run 44950000524) reproduced both digests exactly; the stamp
  stayed at 6388718, the last input-touching commit.
- A comment-only change to one input
  (`shared/99-rowyn-sunshine-uinput.rules`, in the tree built
  by dispatch run 44955771558 at ce90703, stamp 0874171)
  changed exactly 2 of 55 layers: layer 29, the rules file's
  own layer, and layer 49, the build-info stamp layer — a few
  kilobytes of client download instead of ~2.9 GB.

The final build's package manifest is the same 2,407 packages
as the November 19 grouped image, so no archive drift touched
any of the three proofs. One transition cost: the first build
under the fixed epoch re-stamped every layer once — the epoch
value itself moved from the then-current commit time to the
constant — so clients took one full download when the fix
landed. Every build since is incremental by input. A
companion gate in the same workflow now skips the release
entirely for pushes that touch no image input; the identical
digests above are what make that skip safe.

### Reproducibility ledger

Cross-commit stability turns "same inputs, same digest" into a
checkable claim, and the daily rebuild is where it is checked. The
archive is the one input the image does not pin, so the payload
stamps its installed name+version set — `sha256` over the sorted
`dpkg-query` output — into
`/usr/share/rowyn/package-fingerprint` while the dpkg database
still exists (`/var` is deleted afterwards).

The Build job records every build under a registry tag naming both
inputs, `repro-<source revision>-<package-set prefix>`, pointing
at the digest that pair produced. A later build of the same pair
reads that record: the same digest is a quiet day, and any other
digest fails the run with the recorded and rebuilt digests named.
The check sits in the Build job, which `sign` and `promote` need,
so a drifted rebuild is never signed and never advances
`:stable`/`:testing` — the failure is the signal that an unpinned
input (a moved URL, a new download step, a changed build host)
entered the image, and it repeats on every run until that input is
pinned again.

What the ledger cannot catch is inherent: when the package set
moves, the pair is new, so there is no earlier digest to compare.
That case is the layer map's to bound, and the fingerprint at
least makes it visible which of the two happened.

## Daily rebuild cadence

The Ubuntu digest-pin workflow runs daily. On days when the pin
moves, its landed bump dispatches a build as before. On days
when the pin does not move, the run also dispatches one build,
so archive package and security updates are picked up daily
instead of waiting for the next Canonical base republication.
Exactly one dispatch leaves each pin run: the two dispatch steps
key off opposite values of the same committed signal.

This was gated on the double-build proof above, which passed on
2026-11-21, and the cadence was enabled the same day. Enabling
it earlier would have pushed a multi-hundred-megabyte "update"
to every client on quiet days, which is exactly what this work
exists to prevent. With cross-commit stability (above), a
quiet day rebuilds to the identical digest and clients download
nothing — even if unrelated commits landed in between; a day
with archive changes downloads only the layers those changes
touch, and a day whose only commits touched one image input
downloads only that input's layers.

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
