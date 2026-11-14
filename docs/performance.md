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
