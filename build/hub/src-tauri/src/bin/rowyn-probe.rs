//! rowyn-probe — the producer half of the Hub's probe cache.
//!
//! The shared crate's `system::probe` reader/writer library shipped
//! without a driver: the Hub read cache files nothing ever wrote, so
//! probe-backed sections rendered empty. This binary is that driver,
//! following the library's own contract:
//!
//! * `--system` (the root `rowyn-probe.service`): stream-collect every
//!   section except the deferred network-backed ones, persisting each
//!   completed batch immediately — the service's 120 s timeout can kill
//!   a slow cycle without losing the sections that did finish. The
//!   writer makes the system cache world-readable because user-session
//!   readers consume it from under the service's UMask=0077.
//! * user mode (the per-user timer): merge the still-fresh system
//!   sections instead of re-collecting them, collect the rest
//!   (including the deferred flatpak-updates section), and persist to
//!   the user's runtime/home cache.
//!
//! Collection failures are data (JSON null sections), never a crash; a
//! non-zero exit means the cache itself could not be written.

use rowyn_shared::system::probe;
use std::process::ExitCode;

fn main() -> ExitCode {
    let system = std::env::args().skip(1).any(|arg| arg == "--system");
    let mut write_failed = false;
    if system {
        let skip = probe::deferred_system_sections();
        probe::collect_snapshot_streaming(skip, &mut |batch| {
            if probe::update_sections(batch, None, true).is_err() {
                write_failed = true;
            }
        });
    } else {
        let fresh = probe::fresh_system_sections();
        let fresh_keys: Vec<&str> = fresh.keys().map(String::as_str).collect();
        let skip = probe::cacheable_sections_to_skip(&fresh_keys);
        let mut collected = probe::collect_snapshot_streaming(&skip, &mut |batch| {
            if probe::update_sections(batch, None, false).is_err() {
                write_failed = true;
            }
        });
        probe::merge_fresh_sections(&mut collected, fresh);
        if probe::update_sections(&collected, None, false).is_err() {
            write_failed = true;
        }
    }
    if write_failed {
        eprintln!("rowyn-probe: probe cache write failed");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}
