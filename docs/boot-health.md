# Boot health and update rollback

Rowyn replaces the whole operating system with a staged image and reboots into
it. `rowyn-boot-health` is the gate that decides whether the booted image is
actually usable, and moves the machine back when it is not.

It is Rowyn's own implementation for systemd, bootc and Ubuntu. There is no
greenboot, no GRUB boot counter, no ostree state, and no upstream helper: the
image's `/boot` is managed by bootc, so the greenboot rollback story does not
exist here.

## What happens on a boot

A timer (`rowyn-boot-health.timer`, 90 s after boot) runs
`rowyn-boot-health boot` once. For a deployment that has already been
confirmed healthy, nothing else happens — that is the whole cost on a normal
boot.

For a deployment that has never been confirmed, the helper runs three bounded
checks:

| Check | Passes when |
| --- | --- |
| `identity` | `/usr/lib/os-release` still says `NAME="Rowyn"` and `ID=rowyn` |
| `kernel-modules` | `/usr/lib/modules/$(uname -r)` exists and is not empty |
| `desktop` | `graphical.target` and `display-manager.service` are both active within the grace period (90 s) |

Passing records the digest as **known good**. A known-good deployment is never
judged again: a failure after that point is environmental, not the image, and
counting it could only roll back a working system.

Failing increments a per-digest counter. At the third failure the digest is
**quarantined**, `bootc rollback` moves the machine to the previous
deployment, and the helper reboots. Quarantine is permanent until an
administrator clears it, so the same bad image cannot be staged and booted
again by accident.

## Why the machine cannot bounce

Two guards, both recorded per digest in the state file:

* a quarantined digest is never used as a rollback destination;
* each digest gets at most two rollback attempts, the second only after a
  10-minute cooldown.

So a rollback target that is itself broken cannot send the machine back to the
digest that triggered the rollback, and a failed `bootc rollback` is recorded
with its error instead of retrying forever. If both deployments are unusable
the machine stays put and says so.

## What it deliberately does not check

Network, audio, Flatpak, printing, user services and `/etc` policy are out of
scope. A user who stopped a service or unplugged a cable must not trigger a
rollback: that failure would survive the rollback and could only create a
reboot loop. The checks cover invariants the image owns.

If `bootc status` itself is unavailable or answers in an unknown shape, the
boot is recorded as unjudged rather than unhealthy, for the same reason.

## State

`/var/lib/rowyn/boot-health.json`, mode 0644, written atomically (temp file,
fsync, rename, parent fsync) and only by root. It contains image digests,
timestamps, failure counts and reasons — never credentials, hostnames or user
data — so the Hub and `rowyn-report` can read it as an ordinary user.

A corrupt state file is preserved as `boot-health.json.corrupt` and logged; it
is never silently reset.

## Commands

```bash
rowyn-boot-health status                 # recorded state as JSON (no root)
rowyn-boot-health check                  # run the checks, write nothing
sudo rowyn-boot-health boot              # the timer path
sudo rowyn-boot-health clear-quarantine sha256:…
sudo rowyn-boot-health quarantine sha256:… manual
```

Clearing a quarantine only allows the digest to be staged again; it does not
update or reboot anything.

The Hub's boot-health panel runs the same helper: clearing a quarantine there
confirms first, then calls `clear-quarantine -` through the sudoers allowlist
with the digest on stdin, after checking the digest is still quarantined in the
record it reads itself. Reading a quarantine never needs elevation.

## Update policy

`/etc/rowyn/update-policy.conf` (defaults in
`/usr/share/rowyn/update-policy.conf`) adds two keys to the existing
automatic-staging gates:

| Key | Meaning |
| --- | --- |
| `Channel` | `stable` is the only published channel; anything else is refused rather than silently mapped |
| `AllowDowngrade` | `no` refuses a staged image older than the booted one |

The downgrade guard runs in `rowyn-safe-upgrade` before the staged deployment
is finalized. A refused downgrade is quarantined through the same state file,
so if the deployment still boots it is rolled back like any other bad image.
Versions are compared when both are dotted numbers, otherwise image build
timestamps; an unorderable pair is allowed rather than blocking an update.

## Limits

This is a first-boot gate, not a full qualification harness. It cannot see a
desktop that starts but is unusable, and it says nothing about hardware
coverage. Cross-version upgrade and rollback qualification is still tracked
separately in the project status.
