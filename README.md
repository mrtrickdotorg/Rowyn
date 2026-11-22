<div align="center">

<img src="docs/assets/rowyn-logo.png" alt="Rowyn" width="150">

# Rowyn

**An Ubuntu desktop with KDE Plasma and image-based system updates.**

[Try Rowyn](#try-rowyn) · [Installer builds](https://github.com/mrtrickdotorg/Rowyn/actions/workflows/installer-iso.yml) · [Report a bug](https://github.com/mrtrickdotorg/Rowyn/issues)

[![Build](https://github.com/mrtrickdotorg/Rowyn/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/mrtrickdotorg/Rowyn/actions/workflows/build.yml)
[![Installer ISO](https://github.com/mrtrickdotorg/Rowyn/actions/workflows/installer-iso.yml/badge.svg?branch=main)](https://github.com/mrtrickdotorg/Rowyn/actions/workflows/installer-iso.yml)

</div>

## A familiar desktop, a different approach to the system

Rowyn pairs Ubuntu 26.10 with KDE Plasma. You get a familiar desktop with a panel, application launcher, file manager, and settings you can make your own.

The difference is underneath. Rowyn delivers the operating system as a complete image through **bootc**, rather than asking you to maintain the host with individual package upgrades. The installed system and the live installer are built from Ubuntu.

## Apps stay separate from the OS

Use **Discover** to find and install Flatpak apps from Flathub. App updates are separate from system updates, and installing an app does not change the base operating system.

For development tools and command-line packages, **Distrobox** provides Ubuntu containers backed by rootless Podman. You can use Ubuntu's package manager inside those containers without changing the host.

The base system stays focused on the desktop. Your apps, development environments, and personal files live outside the read-only OS image.

## System updates

Rowyn uses bootc to manage complete system images. This gives OS changes their own update path, separate from the apps you install through Discover.

Bootc also provides upgrade and rollback mechanisms. Each staged image is checked the first time it boots, and an image that cannot reach a working desktop is rolled back automatically after three failed boots — see [how update health works](docs/boot-health.md). Cross-version upgrade and rollback testing is still in progress; those recovery paths are not yet qualified for daily-driver use.

## Try Rowyn

> [!IMPORTANT]
> Rowyn is a development preview. Installation and reboot persistence have passed UEFI virtual-machine testing, but hardware coverage is still limited. Start with a virtual machine and a disposable disk.

The current installer supports **x86-64 UEFI with Secure Boot disabled**, using an **unencrypted, whole-disk Btrfs installation**. It needs an internet connection to download the signed system image.

**Installation erases the selected disk.** Dual boot, manual partitioning, disk encryption, BIOS/CSM boot, and Secure Boot are not supported by the current installer.

1. Open a successful run in [Installer builds](https://github.com/mrtrickdotorg/Rowyn/actions/workflows/installer-iso.yml).
2. Download and extract its `rowyn-custom-installer-iso-…` artifact. GitHub may require you to sign in; artifacts expire after seven days.
3. [Verify the signature and checksum](docs/building.md#verify-an-iso) before booting the ISO.
4. Attach the ISO to a UEFI virtual machine with a spare virtual disk, then follow the installer.
5. Let the live system shut down completely before detaching the ISO. Boot from the installed disk afterward.

## Project status and help

VM checks cover installation, networking, the read-only OS, Flatpak apps, rootless Ubuntu containers, and persistence across reboot. That is a starting point, not a claim that every PC or workflow has been tested.

Report problems in [GitHub Issues](https://github.com/mrtrickdotorg/Rowyn/issues). Include the ISO build or image digest, whether you are using a VM or physical hardware, and the steps needed to reproduce the problem. Remove passwords and other private information from logs before sharing them.

Rowyn uses an Ubuntu port of the custom installer it shares lineage with. Rowyn is licensed under [Apache 2.0](LICENSE); upstream components retain their own licenses. It is an independent project, not an official Ubuntu or KDE edition.

<details>
<summary><strong>Technical documentation and contributor resources</strong></summary>

The front page covers using Rowyn. Build instructions, installer internals, signature verification, and CI release details live in the [build and installer guide](docs/building.md).

```bash
./build/fetch-assets.sh   # fetch pinned image-build inputs
./build/validate.sh       # run source and CI contract checks
sudo podman build -t rowyn:local .
```

Validation needs Bash, Git, Python 3 with PyYAML, `shellcheck`, `skopeo`, and `actionlint`. The last three are separate tools on most distributions, and validation stops early when any is missing. GitHub Actions installs a checksum-pinned `actionlint` release in every workflow that validates, so workflow expression and `needs`-context mistakes fail validation instead of silently disabling a gate. The update-policy signature tests verify checked-in signatures offline.

The guide also documents optional Ubuntu runtime checks and pinned-source installer tests. Source validation does not replace a real installation and reboot test.

</details>

