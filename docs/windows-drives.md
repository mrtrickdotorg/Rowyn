# Windows drives on Rowyn (NTFS and exFAT)

Rowyn reads and writes the drives you bring from Windows using the Linux
kernel's own drivers — `ntfs3` for NTFS and `exfat` for exFAT. There is no
separate driver to install, and no FUSE layer in between: Dolphin mounts a
Windows partition or a USB drive with the same click as any other volume,
and files copy at native speed.

## What works

- Internal NTFS partitions (including a Windows installation's data
  partitions) mount read-write.
- exFAT USB drives and SD cards — the format most cameras and large USB
  sticks use — mount read-write.
- Long filenames, large files (>4 GB on exFAT/NTFS), and Unicode names are
  handled by the kernel drivers.

## The Windows fast-startup caveat (important)

Windows' "fast startup" does not fully shut Windows down: it hibernates
the kernel session and leaves every NTFS volume marked *in use*. When a
volume is in that state, Rowyn mounts it **read-only** rather than risk
corrupting Windows' suspended session. Nothing is wrong with the drive.

To get read-write access, do one of these in Windows:

1. Turn fast startup off: Control Panel → Power Options → "Choose what
   the power buttons do" → "Change settings that are currently
   unavailable" → untick **Turn on fast startup**. Then shut down fully.
2. Or restart Windows (a restart always closes volumes cleanly), and
   boot Rowyn from that restart instead of from a shutdown.
3. The same applies to BitLocker: a BitLocker-locked volume cannot be
   opened by Rowyn at all until it is unlocked or decrypted in Windows.

## What Rowyn will not do

- Rowyn never writes to a volume Windows left hibernated. The read-only
  mount is deliberate.
- The Rowyn installer refuses to *resize* a Windows partition whose NTFS
  dirty bit is set — the same hibernation state makes shrinking unsafe,
  and the installer refuses instead of guessing. (Erasing a whole disk
  you selected is a different, explicit choice and is unaffected.)
- NTFS permissions (ACLs) are not enforced on Linux. Files copied from a
  Windows drive arrive owned by you; files copied back may not keep their
  Windows permissions.

## Sharing files with Windows machines on your network

The Samba packages ship in the image: Dolphin's *Properties → Share* tab
shares a folder to Windows machines (via `kdenetwork-filesharing`), and
`smbclient`/Dolphin's `smb://` locations browse and mount shares other
machines offer.
