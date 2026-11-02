# Building Rowyn and its installer

Rowyn has two build products: an Ubuntu bootc desktop image and an Ubuntu live ISO that installs a specific signed image. The live system, compiler toolchains, and ISO assembly environment use Ubuntu 26.10.

## Build the OS image

The Ubuntu base image is pinned by digest in `Containerfile` and in
`.github/workflows/installer-iso.yml`; `build/ubuntu-pin-bump.py` resolves the
`ubuntu:26.10` tag and moves both pins together. `.github/workflows/ubuntu-pin.yml`
runs that daily, validates the result and commits it to `main`, so the normal
main-branch runs rebuild, smoke-test and sign a freshly pinned payload. To bump
it by hand, run `python3 build/ubuntu-pin-bump.py --apply "$(python3
build/ubuntu-pin-bump.py --resolve)"` and then `./build/validate.sh`.

From the repository root, fetch the pinned offline inputs and build with Podman:

```bash
./build/fetch-assets.sh
sudo podman build -t rowyn:local .
```

### Accounts and SSH

Images contain no login accounts; the installer creates the only one. SSH is installed but disabled, and if you enable it, password and root logins stay off. Systems installed from images that still had the old `rowyn` test account have it locked on their first boot of a newer image by `rowyn-retire-spike-account.service`.

The Containerfile builds bootc from the fetched source with offline Cargo dependencies. KDE Plasma and native OS dependencies are image content. Flatpak apps and rootless Ubuntu development containers are separate from the read-only host.

### Development workspace

Hub's Development page manages one dedicated rootless Distrobox named
`rowyn-ubuntu-dev`, based on Ubuntu 26.10. It refuses to take ownership of an
existing box with that name rather than modifying a box the user already
owns. The image reference is digest-pinned, and the container installs the
USN-8996-1 (CVE-2026-109518) apt before any preset installs packages, so
package installs never run under the vulnerable archive client. Distrobox shares the user's home directory, so it is a development
environment, not a security sandbox. Creating or deleting the box requires an
explicit user action; deleting it removes the container and its installed
packages, not files in the shared home directory. The feature does not install
packages on the native host. No live container operations are part of source validation.

### Daily-driver integrations

- **Development presets:** Git/build tools, Python, Node, and Rust use fixed Ubuntu package lists. The Hub previews the plan and requires confirmation; installation and verification run inside the owned development container, not on the host. Cancelling may leave partial package or apt state and never implies rollback.
- **Dolphin:** the directory context menu opens an existing owned environment at a canonical folder within the shared home directory. It does not create a container. Remote locations, files, and folders outside that boundary are refused.
- **Updates:** Home and Updates share a passive status banner. A user-session timer sends one notification per staged image digest, without staging an update or forcing a restart.
- **Backups:** Kup owns backup-plan editing, destination selection, scheduling, and backup execution. The Hub reads `kuprc` on request to show plan folders, destination, backup type, and Kup's recorded completion time. A configured plan or recorded timestamp does not prove the destination is connected or that a restore will succeed. **OS rollback does not restore personal files.** Versioned backups and mirrors have different deletion/history behavior.
- **Troubleshooting:** Diagnostics provides focused read-only Bluetooth, Wi-Fi, audio-service, and display findings, with explicit links to KDE settings. These links request a panel; they do not claim to repair hardware.
- **Defaults & Access:** Apps links to KDE Default Applications, File Associations, and Application Permissions. Portal-mediated file selection and broad persistent Flatpak overrides are distinct. The Hub does not grant extra filesystem access automatically.
- **Getting started:** the optional Dashboard checklist persists manual completion/skips, can be dismissed, and can always be resumed. It launches no setup operations automatically and reports when preferences cannot be saved.
- **Support reports:** explicit collection preserves validated public build/image provenance while redacting other sensitive output. Preview before copying or saving; no report is uploaded automatically. Native report files are private and do not overwrite an existing file.

The image explicitly includes `kup-backup`, `bup`, `rsync`, and `kde-config-flatpak`. Ubuntu 26.10 ships Kup as `kcm_kup` and Flatpak permissions as `kcm_app-permissions`; these are checked against installed module metadata rather than guessed from older Ubuntu releases. CI passes the exact source revision to `shared/write-build-info.py`; a local build without `ROWYN_SOURCE_REVISION` records an unknown revision rather than inventing one.

Automated validation uses fixtures for package execution, configuration, commands, and redaction. Real preset installation/cancellation, Dolphin/Wayland launching, backup/restore, desktop notification delivery, and physical-device behavior remain installed-system qualification tasks; automated tests do not certify them.

### Storage, power, and recovery care

Diagnostics includes separate, explicit read-only storage and power refreshes. Storage uses writable backing-filesystem capacity rather than the composefs `/` size. Shared filesystems and Flatpak/Podman objects must not be added into a promised reclaimable total. Per-app installed sizes are estimates; missing tools, denied reads, and unknown sizes remain visible. Downloads are not measured or cleaned automatically. Review app removal in Apps or the owned environment in Development: neither storage refresh nor navigation performs cleanup. Apps requires an additional acknowledgement before its existing removal flow; that flow selects the system copy first when both scopes contain the same app, and system removal affects other users. Saved app data and personal files are not part of that removal.

Power care reports available battery measurements, charge-threshold capability, the active power profile, and kernel sleep capabilities. These are observations, not successful suspend/resume tests. An absent attribute is distinct from a failed read. The Hub does not write charge limits, change profiles, suspend the machine, or enable hibernation. Rowyn's existing disabled hibernate, hybrid-sleep, and suspend-then-hibernate policy is unchanged; KDE Power settings remain the place for explicit user configuration.

Backups provides manual versioned-backup and mirror recovery guidance. Use Kup's browser for versioned backups and Dolphin for mirror files; first restore one recognizable file into a **new separate folder**, never over the original. The Hub creates no restore directories, executes no restore commands, and does not certify recoverability from a recorded completion timestamp.

Updates explains the running, staged-next-boot, and previous rollback deployments using cached status. Published history is requested only on an explicit click and displayed only for an exact full-digest match to the selected deployment. Unpublished or unavailable notes are not guessed from a channel or version. Rollback replaces OS content after restart; it is not a restore of home files, app data, or development containers, and mutable configuration can persist under bootc policy. No new updater, automatic rollback, or reboot action is introduced by this view.

## Validate source changes

Requirements include Git, Python 3 with PyYAML, Bash, and ShellCheck. Run from the repository root:

```bash
./build/validate.sh
```

The gate checks shell syntax, ShellCheck errors, workflow YAML, Ubuntu-only build guards, image and installer trust contracts, and regression tests. Advisory ShellCheck warnings are reported separately.

Additional integration checks use a local upstream checkout containing the pinned installer commit and the isolated Ubuntu runtime fixtures:

```bash
export ROWYN_TEST_UPSTREAM_REPO=/path/to/upstream
./build/validate.sh
```

`ROWYN_TEST_UBUNTU_APP_POLICY=1` and `ROWYN_TEST_UBUNTU_NETWORK_POLICY=1` enable the Ubuntu application and network policy fixtures. `ROWYN_TEST_UBUNTU_PACKAGE_CLOSURE=1` enables the more expensive package-closure check. See the tests for fixture image names and prerequisites; these checks need Podman and are not all enabled by default.

Source validation does not replace booting the actual signed ISO, installing to a disposable disk, and booting that disk without the ISO.

## Build a custom installer ISO

Requirements: Podman, Git, Python 3 with PyYAML, network access, ample disk space, and a local upstream checkout containing commit `2baf8496b5aa904c0784f4290df743ee17edae48`.

Choose an existing signed Rowyn payload. Obtain its digest and original source commit from that payload's build metadata; do not resolve a mutable tag as the installation source. Replace the example values below before running:

```bash
export TMPDIR="$HOME/.cache/rowyn"
mkdir -p "$TMPDIR"
export SOURCE_TAG='REPLACE_WITH_PAYLOAD_SOURCE_COMMIT'
export IMAGE_DIGEST='sha256:REPLACE_WITH_VERIFIED_PAYLOAD_DIGEST'
export IMAGE_REF="ghcr.io/mrtrickdotorg/rowyn:${SOURCE_TAG}"
export UPSTREAM_SOURCE_DIR=/path/to/upstream
export UPSTREAM_INSTALLER_COMMIT=2baf8496b5aa904c0784f4290df743ee17edae48
export UBUNTU_LIVE_IMAGE=docker.io/library/ubuntu@sha256:262df65c0ec6bf8d9dcc87454f4330a05fae4b97825f45854af3b136666584c2
ROWYN_CUSTOM_INSTALLER_PREFLIGHT=1 ./build/custom-installer-iso.sh
./build/custom-installer-iso.sh
```

Use a writable scratch filesystem rather than a RAM-backed directory. The build exports source and creates its working files beneath `TMPDIR`. OS compilation and filesystem tooling run in Ubuntu containers, without a host chroot or host package installation. ISO assembly receives output and scratch directories, not host block devices.

### Ubuntu live environment

`build/ubuntu-live.Containerfile` starts its compiler and live stages from the digest-pinned official Ubuntu image. Apt supplies Rust, Cargo, Node, GTK/WebKit, Plasma, Casper, initramfs-tools, GRUB, xorriso, and SquashFS tools. It does not execute an upstream OS Containerfile or ISO wrapper.

The Apache-2.0 upstream installer daemon, typed execution helper, Tauri shell and web frontend, shared crate, and vendored glib dependency are exported from the pinned commit using an allowlist and `git archive`. The source checkout is not modified. The Slint UI binary is not built or installed. Licenses and the modification notice ship in `/usr/share/doc/rowyn-installer`.

`build/ubuntu-installer-source.py` adapts account groups (`sudo`, not `wheel`), tool paths, locale and keyboard configuration, and image selection for Ubuntu. Kernel selection cannot rewrite the immutable image digest.

Casper and initramfs-tools build the live initramfs. The installed system retains the payload's matching kernel and bootc/dracut initramfs. The Ubuntu-built bootc binary and `/usr/lib/bootc` helpers come from the exact signed payload; the live stage checks their Ubuntu runtime dependencies before ISO assembly. Installation uses the composefs backend and systemd-boot.

Ubuntu's `casper(7)` manual and the Ubuntu Community Help `LiveCDCustomization` and `LiveCDCustomizationFromScratch` guides describe the live layout: kernel and SquashFS under `/casper`, plus `.disk/info`. The payload is not used as the live root filesystem.

### Installation scope

The enabled path is x86-64 UEFI, Secure Boot disabled, whole-disk Btrfs, without encryption. BIOS/CSM, Secure Boot/MOK enrollment, manual partitioning, Windows/NTFS resize, partition replacement, free-space installation, and TPM encryption are disabled. The daemon rejects unsupported paths as well as the UI, including the separate manual-disk mutation API.

Recorded acceptance for ISO run `40594582203` covered installation to a disposable UEFI VM disk, disk-only desktop boot, networking, Flatpak apps, rootless Ubuntu containers, the read-only OS, and persistence across reboot. That result applies to that tested artifact. Cross-version bootc upgrade and rollback qualification remains outstanding, and VM testing does not establish physical-hardware compatibility.

### Storage and signature safeguards

The ISO workflow verifies the fixed payload signer before privileged storage setup. The live daemon verifies the exact compiled-in Rowyn source, target, and Cosign signer in Prepare, before storage mutation, and checks again in Image. Runtime binary, library, and CLI checks also run before storage changes.

The installation needs anonymous registry access and network connectivity because payload layers are not embedded in the ISO. Casper `/cdrom` and `/isodevice` mounts are protected, including loop ISO backing files resolved to physical media. Unresolved backing devices fail closed. The privileged daemon retains its sandbox and authenticated live-user socket; only the argument-free session launcher is passwordless.

Keep the live ISO attached until the live system has completely shut down. Removing media while Casper still uses its SquashFS root causes I/O failures. Detach it only before starting the installed system.

## Verify an ISO

Extract the ISO, checksum, and signature bundle into the same directory. Set `ISO` to the actual downloaded basename, then verify the signed checksum manifest before checking the ISO:

```bash
ISO='rowyn-custom-installer-REPLACE_WITH_SOURCE_COMMIT.iso'
cosign verify-blob \
  --bundle "${ISO}.sigstore.json" \
  --certificate-identity-regexp '^https://github.com/mrtrickdotorg/Rowyn/\.github/workflows/installer-iso\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "${ISO}.sha256"
sha256sum -c "${ISO}.sha256"
```

Both commands must succeed. A checksum alone does not authenticate its source.

### Verify an ISO downloaded from R2

When the R2 secrets are configured, every ISO build is also uploaded to Cloudflare R2 under two names: an immutable build set `rowyn-live-stable-<YYYYMMDD>-<sha8>-<run>-<attempt>.iso` and a channel pointer `rowyn-live-stable.iso` that is overwritten by each build, so a fixed URL always serves the newest ISO. Each set has `.sha256`, `.sigstore.json` and `.json` companions. The R2 names differ from the GitHub artifact name, so each set carries its own checksum file and its own Cosign bundle, signed by the same workflow identity. Download the ISO, `X.iso.sha256` and `X.iso.sigstore.json` into one directory, keep the ISO's filename, and run:

```bash
ISO='rowyn-live-stable.iso'   # or the immutable rowyn-live-stable-...-....iso name
cosign verify-blob \
  --bundle "${ISO}.sigstore.json" \
  --certificate-identity-regexp '^https://github.com/mrtrickdotorg/Rowyn/\.github/workflows/installer-iso\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "${ISO}.sha256"
sha256sum -c "${ISO}.sha256"
```

### R2 setup

The upload step skips with a notice when the secrets are missing, so builds never fail for lack of them. The maintainer sets, under repository Settings, Secrets and variables, Actions:

- Secrets: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and either `R2_ACCOUNT_ID` or `R2_ENDPOINT_URL` (`https://<account-id>.r2.cloudflarestorage.com`).
- Variables: `R2_BUCKET` (default `rowyn-releases`) and optionally `R2_PUBLIC_URL`, the bucket's public base URL (custom domain or `r2.dev`) used to print download links in the run summary. The bucket must be made publicly readable in Cloudflare for those links to work.

After each successful upload, retention keeps the newest 5 immutable build sets per channel (ordered by the date, run and attempt in the key name) and deletes older ones whole. Channel pointers and every key that is not a Rowyn immutable build, including `kyth-live-*` objects in a shared bucket, are never deleted. A retention failure does not fail the build.

The assembler emits the ISO, source metadata, El Torito/system-area report, and SquashFS report. CI signs and verifies the portable checksum manifest and uploads the signature bundle. Apt resolves packages from Ubuntu repositories, so the package manifest is recorded but the build is not bit-for-bit reproducible.

## CI and release flow

`build.yml` (Rowyn CI) validates, builds, smoke-tests, publishes, and signs the payload on trusted main pushes affecting payload inputs, or manual main dispatch. Pull requests validate only; they cannot publish images or request ISO releases. The GHCR package is linked to `mrtrickdotorg/Rowyn`, and that repository has `write` under the package's Manage Actions access. Trusted publishing/signing jobs use the workflow-scoped `GITHUB_TOKEN` with `packages: write`; no PAT is needed. The Containerfile also declares `org.opencontainers.image.source` for this repository.

The fixed payload signing identity is:

```text
https://github.com/mrtrickdotorg/Rowyn/.github/workflows/build.yml@refs/heads/main
```

`installer-iso.yml` runs after a successful trusted main image release. It checks the upstream run through the GitHub API, reads only that run's image metadata artifact, checks its SHA/run binding, and verifies the image signer before privileged work. The installation source is the immutable digest at `ghcr.io/mrtrickdotorg/rowyn@sha256:…`; the installed updater targets `ghcr.io/mrtrickdotorg/rowyn:stable`. Tags are provenance labels, not installation-source selectors.

### Promotion collision guard

The promoted stable image is a derivative with `/usr/share/rowyn-registry-migration-destination-v1`, a new rootfs file absent from the image it is derived from. CI verifies the marker is absent in the source and present in the promoted image, verifies both signatures and an anonymous pull, then checks the stable digest.

This unique rootfs content is required: the pinned bootc 1.17.3 implementation rejects `bootc switch` when the target composefs fs-verity digest matches any booted, staged, rollback, or pinned deployment. A tag, repository path, signature, or OCI manifest digest change alone does not make a switch safe. The automatic and manual helpers use `bootc switch` only from the old registry — migrating a deployment booted on the old image path onto the canonical one — and ordinary `bootc upgrade` once the system is already on the destination, avoiding repeated switch collisions on the current deployment. Rollback/re-switch behavior remains a separate runtime qualification; do not claim it has been exercised by source tests.

ISO-only changes validate the installer without rebuilding the payload or selecting a mutable latest image. To retry against an existing signed payload, replace both values with its exact digest and original source commit:

```bash
gh workflow run installer-iso.yml --ref main \
  -f image_digest='sha256:REPLACE_WITH_VERIFIED_PAYLOAD_DIGEST' \
  -f source_sha='REPLACE_WITH_PAYLOAD_SOURCE_COMMIT'
```

Automatic runs require the upstream image metadata artifact, retained for seven days. Manual retries do not depend on that artifact. ISO artifacts are also retained for seven days. GitHub-hosted builds use the narrow rootful Podman wrapper in the host cgroup namespace; local builds use rootless Podman.

## Attribution

Rowyn uses an Ubuntu port of the upstream custom installer. Upstream installer source is Apache-2.0; Tauri is MIT/Apache-2.0; the vendored glib Rust backport declares MIT. The live installer includes dependency license texts.
