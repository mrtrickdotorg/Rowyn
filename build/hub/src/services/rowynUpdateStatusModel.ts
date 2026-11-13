import type { UpdatesSnapshot } from "./liveData";

export type UpdateBannerState = "unavailable" | "current" | "available" | "staging" | "staged" | "restartready" | "blocked" | "failed" | "deferred" | "disabled";

// Reasons the update service reports when it cannot be read at all.
const READ_FAILURE = /could not read|not installed|no such file|timed out|unavailable/i;

export interface UpdateBannerProjection {
  state: UpdateBannerState;
  label: string;
  guidance: string;
}

/** Pure display projection. A failed read is represented separately from a successful empty snapshot. */
export function projectUpdateBanner(snapshot: UpdatesSnapshot | null, readFailed = false): UpdateBannerProjection {
  if (readFailed) return { state: "failed", label: "Update status unavailable", guidance: "Could not read update status. Refresh to try again." };
  if (!snapshot || (!snapshot.status && !snapshot.pending && !snapshot.health)) {
    return { state: "unavailable", label: "Update status unavailable", guidance: "No update status is available yet. Refresh to check again." };
  }

  const status = snapshot.status;
  const health = snapshot.health;
  const pending = snapshot.pending;
  const staged = Boolean(status?.staged) || pending?.bootc === "staged";
  const check = status?.check_state ?? "";
  // bootc missing or unreachable surfaces as a blocked_reason too. That is a
  // read failure, not a safety hold, and must not read as "Update blocked".
  if (check === "error" || check === "failed" || READ_FAILURE.test(status?.blocked_reason ?? "")) {
    return { state: "failed", label: "Update status unavailable", guidance: status?.blocked_reason || status?.detail || "The update service did not respond. Refresh to try again." };
  }
  if (check === "blocked" || Boolean(status?.blocked_reason) || health?.status === "quarantined") {
    return { state: "blocked", label: "Update blocked", guidance: status?.blocked_reason || status?.detail || health?.detail || "An update is held back for safety. Review Updates for details." };
  }
  if (staged) return { state: "restartready", label: "Restart required", guidance: "An update is staged and will apply when you restart. Open Updates to review details." };
  if (["busy", "checking"].includes(check)) return { state: "staging", label: "Update check in progress", guidance: "Update status is being checked. Open Updates for details." };
  // A deferred check never ran, so it is neither current nor failed; a
  // policy-disabled watcher is a deliberate off, not a missing reading.
  // The backend detail carries the specifics (reason, last check time).
  if (check === "deferred") return { state: "deferred", label: "Update check deferred", guidance: status?.detail || "The last automatic update check was deferred. Open Updates for details." };
  if (check === "disabled") return { state: "disabled", label: "Automatic checks off", guidance: status?.detail || "Automatic update checks are disabled. Open Updates to check manually." };
  if (check === "error" || check === "failed") return { state: "failed", label: "Update status unavailable", guidance: status?.detail || "The update check failed. Refresh to try again." };
  if (check === "staged") return { state: "staged", label: "Update staged", guidance: "An update is staged. Open Updates to review restart information." };
  // A remote digest may identify the currently running image, and the pending
  // map always contains keys even when its counts are zero. Neither proves an update.
  if (check === "available") return { state: "available", label: "Update available", guidance: "An update is available. Open Updates to review it; nothing will be installed from this banner." };
  if (check === "uptodate" || check === "current") return { state: "current", label: "System is up to date", guidance: status?.detail || "No system update is currently available." };
  return { state: "unavailable", label: "Update status unavailable", guidance: status?.detail || "Refresh to check update status." };
}
