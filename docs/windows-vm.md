# A Windows 11 virtual machine on Rowyn

Some Windows software has no Linux path and no Bottles path — tax
suites, certain exam and CAD tools, a work app pinned to Windows.
For those, Rowyn ships the basics of a real Windows 11 virtual
machine in the image: **virt-manager**, **qemu-system-x86**, **swtpm**
(a software TPM) and **ovmf** (UEFI firmware). Nothing to install.

## Creating the VM

1. Download a Windows 11 ISO from Microsoft (microsoft.com →
   Software Download → Windows 11 disk image). Also download the
   **virtio-win** driver ISO from Fedora's people site
   (fedorapeople.org → groups → virt → virtio-win) — Windows needs
   these drivers for the fast virtual disk and network devices.
2. Open **Virtual Machine Manager** (virt-manager). Your user needs
   to be able to run VMs; virt-manager will say so if a permission
   is missing.
3. New VM → Local install media → pick the Windows ISO. Give it at
   least 4 CPU cores, 8 GB RAM and 64 GB of disk if you can spare
   them.
4. On the final screen, tick **Customize configuration before
   install**, then:
   - **Overview → Firmware:** choose the `OVMF … secboot` option
     (Secure Boot capable). Windows 11 setup checks for this.
   - **Add Hardware → TPM:** type **Emulated**, model **TIS**,
     version **2.0**. This is swtpm, and it satisfies Windows 11's
     TPM requirement honestly — it is a real TPM implementation in
     software, not a registry bypass.
5. Begin installation. When Windows setup cannot see the disk, choose
   **Load driver**, mount the virtio-win ISO (CDROM 2), and load the
   `viostor` driver for Windows 11; after setup, run the virtio-win
   guest tools installer from that ISO so networking, display and
   clipboard integration work properly.

## Honest limits

- A VM is a second computer living in a window: it needs its own
  Windows licence, its own updates, and real RAM/CPU/disk budget.
- **Gaming in the VM is not the gaming path.** Without GPU
  passthrough (below), the VM's graphics are emulated and slow;
  Proton/Steam on Rowyn itself is faster and is what the Play page
  is for. Kernel-level anti-cheat generally refuses VMs entirely.
- Keep the VM's disk image in your Home folder and back it up like
  any other file (the Hub's Backups page covers Home).

## Advanced, stretch: GPU passthrough

Passing a physical GPU through to the VM (VFIO) gives near-native
graphics, at real cost: the GPU leaves the host for the VM's whole
session, it needs a second GPU (or integrated graphics) for the
host display, IOMMU grouping decides whether it works at all on a
given motherboard, and Rowyn's signed-image model means the setup
lives in bootloader configuration you maintain yourself. It is a
documented technique (the Arch Wiki's "PCI passthrough via OVMF"
guide is the reference), but it is **not** a Rowyn-supported
feature: no Hub surface sets it up, and a Rowyn update will not
preserve hand-tuned passthrough kernel arguments. Treat it as a
weekend project for a machine with two GPUs, not as the way to play
games on Rowyn.
