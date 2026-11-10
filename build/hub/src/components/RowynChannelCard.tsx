import { useEffect, useState } from "react";
import { fetchChannelRaw, invokeBootcSwitchBranch } from "../services/liveData";

/** Rowyn update channels (Pass 2): this machine follows one tag of the
 * canonical image — stable or testing. The switch runs through the
 * Hub's existing branch-switch action (the bootc guard performs the
 * switch and records the choice in the update policy, so automatic
 * staging follows the same channel). Read-only except for the switch
 * buttons themselves. */
export function RowynChannelCard() {
  const [channel, setChannel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Reading the current channel…");

  useEffect(() => {
    let live = true;
    void fetchChannelRaw().then((raw) => {
      if (!live) return;
      setChannel(raw);
      setMessage(raw
        ? `This machine follows the ${raw === "testing" ? "Testing" : raw === "stable" ? "Stable" : raw} channel.`
        : "The current channel could not be read from the booted deployment.");
    });
    return () => { live = false; };
  }, []);

  const switchTo = (target: string) => {
    setBusy(true);
    void invokeBootcSwitchBranch(target)
      .then((result) => {
        setMessage(result);
        return fetchChannelRaw();
      })
      .then((raw) => { if (raw) setChannel(raw); })
      .catch((error: unknown) => setMessage(error instanceof Error ? error.message : String(error)))
      .finally(() => setBusy(false));
  };

  return <section className="glass dashboard-card">
    <h3>Update channel</h3>
    <p className="card-copy">Rowyn publishes one image on two channels. <strong>Stable</strong> is the default. <strong>Testing</strong> follows the same verified builds as they land — today every green build promotes to both tags, and a published cadence commitment for Stable is forthcoming (see the update-channels guide). Switching stages the other channel's current image; it applies on the next reboot, and automatic staging follows the channel you pick.</p>
    <p className="card-copy" role="status">{message}</p>
    {channel && <div className="home-actions">
      {channel !== "testing" && <button className="action-button" disabled={busy} onClick={() => switchTo("testing")}>Switch to Testing</button>}
      {channel !== "stable" && <button className="action-button" disabled={busy} onClick={() => switchTo("stable")}>Switch to Stable</button>}
    </div>}
  </section>;
}
