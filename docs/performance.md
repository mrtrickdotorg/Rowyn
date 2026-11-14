# Performance: measure first, then quicken

Rowyn's daily-driver goal is "super fast and stable". This document is
the standing record of how Rowyn measures speed, what the payload does
about it, and the decisions behind both. Nothing here claims a speedup
that was not measured: image-side evidence is structural (configs ship,
units are set, suites are green); before/after numbers come from real
hardware through the baseline harness below.

## The baseline harness

`rowyn-performance-record` (timer `rowyn-performance.timer`: 2 minutes
after boot, then every 15 minutes) samples the current boot into
`/var/lib/rowyn/performance/history.json`, keeping the newest 30 boots:

- **Boot timing** parsed from `systemd-analyze`: firmware, loader,
  kernel, initrd and userspace splits, the total, and when the session
  target was reached. Segments a platform does not report (a VM has no
  firmware split) stay absent — never estimated.
- **Suspend statistics** from `/sys/power/suspend_stats` (success/fail
  counters for the current boot) — absent when the kernel does not
  expose them.

The Hub's Diagnostics page projects the history as three read-only
readings next to the update health record: **Boot time** (latest boot),
**Suspend / resume** (success ratio across recorded boots, flagged when
any suspend failed) and **Update cycle** (last check time and duration,
and when an update was last staged, from the update watcher's own
status record — an apply duration is not recorded anywhere, so none is
shown).

What only a real install can produce: the actual boot seconds, the
suspend ratio on real hardware, and any before/after comparison. The
first baseline on a new install is the *second* boot after the update
that ships the recorder (the first boot has no recorder running yet —
its timing is never back-filled or guessed).

## Boot-path audit (2026-11-14)

The payload's boot surface is small and every item was decided
explicitly; `test_boot_units.py` pins the sets below so a change has
to update the audit to land.

**Changed — masked:**
| Unit | Why |
|---|---|
| `apt-daily{,-upgrade}.{service,timer}` | Periodic apt churn (network + /var) for package state the image model makes meaningless; host apt is guidance-blocked. |
| `unattended-upgrades.service` | Would download and attempt deb upgrades against an immutable /usr. Inert if the package is absent; masked regardless. |
| `motd-news.{service,timer}` | Fetches Canonical news/marketing for the login banner. |

**Already in place (verified, kept):** `systemd-networkd{,-wait-online}`
and `ufw` masked (NetworkManager and firewalld own their roles);
`NetworkManager-wait-online` **disabled, deliberately not masked** —
the update watcher and the Flatpak preinstall unit order themselves
`After/Wants network-online.target`, and masking is one step further
than the evidence justifies. The initramfs is already minimal for a
generic-hardware image (`hostonly=no` is the deliberate price of one
image for every machine; only the bootc and plymouth modules are
added, and GPU drivers are explicitly omitted so `rowyn-gpu-select`
decides in the real root).

**Audited, kept unchanged:** `fwupd-refresh.timer` (firmware metadata
is a stability input, and the fetch is small), `fstrim.timer` (SSD
health), `man-db`/`plocate-updatedb` (deprioritized, not removed —
see below), CUPS/avahi (socket-activated; ~zero boot cost, printing
must just work), and the KDE session's package autostarts — the
payload ships exactly one of its own (`rowyn-first-run`, marker-gated
to the first login). Akonadi/PIM autostarts belong to their packages,
PIM is payload-native by design, and its agents are D-Bus activated
on demand; removing or delaying them would trade a working mail
client for unmeasured milliseconds.

**Honest limit:** which of these masks saves how many seconds is a
number only a real install can produce — the harness's boot split is
exactly how it will be seen (timer-driven apt work does not appear in
the boot split at all; its cost was jitter and network/disk churn
during the session, which is why it was removed on principle).

## Memory pressure

Established state (shipped before this audit, verified by
`test_memory_pressure_contract`):

- **zram swap** via `systemd-zram-generator`: `zram-size =
  min(ram / 2, 8192)`, zstd, priority 100 (performance-defaults.sh).
  Half of RAM capped at 8 GiB is the distro-typical balance: enough
  compressed headroom (≈3:1) for a game plus desktop without letting
  swap eat the page cache on large-RAM machines.
- **Sysctl pair** that makes zram behave: `vm.swappiness = 180`
  (prefer cheap zram paging over dropping cache) and
  `vm.page-cluster = 0` (no swap readahead on a RAM device).
- **systemd-oomd** is installed and enabled with desktop-tuned policy
  (desktop-tuning.sh): kills at 65% memory pressure sustained 40 s and
  85% swap use, applied to `user.slice` and `user@.service`. Ubuntu's
  50%/20 s defaults fired during game loading screens; the raised
  limits are what make swappiness 180 safe — oomd sheds the worst user
  cgroup before the kernel OOM killer picks something session-critical.
  The contract test fails if zram's aggressive swappiness ever ships
  without oomd enabled alongside it.

## Background-noise discipline

- **Baloo**: filename (basic) indexing on, content indexing off
  (`/etc/xdg/baloofilerc`). The content extractor — which opens and
  parses every document — is Baloo's CPU/IO storm; filename search in
  Dolphin and KRunner keeps working without it. Excluded from the
  crawl: `~/snap`, `~/VirtualBox VMs`, `~/VMs` and container storage
  (hidden trees are skipped by Baloo anyway). **Trade-off, chosen
  deliberately:** full-text file-content search no longer returns
  results (filename matches still do). KMail/Akonadi mail search uses
  Akonadi's own index and is unaffected.
- **Periodic re-indexers yield**: `plocate-updatedb` and `man-db` keep
  their timers but run at Nice 10, idle I/O scheduling and
  CPUWeight/IOWeight 25 via drop-ins, so a database rebuild can never
  stutter the foreground. Inside the session, `user.slice` already
  carries CPUWeight/IOWeight 1000 against system.slice's 100.
- **Not adopted**: ananicy — the drop-in route covers the offenders
  actually present in this payload; adding a rules daemon for two
  units would be machinery without a problem.
