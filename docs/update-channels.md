# Update channels: stable and testing

Rowyn publishes **one image on two channels**:

- **stable** — the default channel; every Rowyn install follows it
  unless its owner switches.
- **testing** — the fast ring, for people who want each verified
  build as it lands and are willing to report what breaks.

Both are tags of the same image (`ghcr.io/mrtrickdotorg/rowyn`) and
both are promoted from the **same signed, verified digest**: the
pipeline builds once, signs once, verifies the signature, and only
then points both tags at that digest. Testing is never a different,
less-checked build.

## The honest cadence note

Today, **every green build promotes to both tags at once** — there
is no soak period between testing and stable yet. The channels
differ in which tag a machine follows, not (yet) in how old the
build it receives is. A published cadence commitment — how long a
build must sit on testing before stable advances — is forthcoming
policy; this document will state it when it exists rather than
inventing one now.

## Switching channels

In the Hub: **Updates → Update channel** shows the channel the
booted deployment follows and offers the switch to the other one.
Underneath:

- the Hub runs its fixed branch-switch action, which calls
  `rowyn-bootc-guard switch-testing` (or `switch-latest` for
  stable) — a fixed-argv, passwordless-sudo gateway; no other image
  reference can be requested through it;
- the guard switches (or upgrades, when already on the target tag),
  and records the choice as `Channel=` in
  `/etc/rowyn/update-policy.conf` — seeded from the shipped default
  the first time — so the automatic stager follows the same channel
  afterwards;
- `rowyn-safe-upgrade` (manual staging from the Hub, and the
  watcher's automatic staging) reads that policy file and stages
  from the followed channel's ref; a machine whose booted tag
  disagrees with the policy channel stages a switch, so the two can
  never silently drift apart.

Switching back is the same action in the other direction; nothing
about the machine's data changes either way, and every deployment
keeps bootc's normal rollback.

Because both tags point at one digest, the switch usually finds the
machine **already running the target channel's build** — only the
tag its deployment carries differs, and bootc refuses to "switch"
to the deployment already booted. That case completes as a channel
change, not an error: the guard (and the stager) verifies that the
target tag's digest is the booted image's own digest, records the
channel, and reports *Already running this build; channel set to
…* — future updates then follow the newly recorded channel, and
the next promoted build stages normally.
