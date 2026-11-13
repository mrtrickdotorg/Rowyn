import { useEffect, useRef, useState } from "react";
import { productionDebClient, rowynDebInspect, rowynDebState } from "../services/rowynDeb";
import {
  createJobController,
  deleteConfirmation,
  describeState,
  installConfirmation,
  removalConfirmation,
  removalOperation,
  signatureText,
  type DebApp,
  type DebInspectReport,
  type DebJob,
  type DebJobController,
  type DebState,
} from "../services/rowynDebModel";

type PendingAction =
  | { kind: "install" }
  | { kind: "remove"; app: DebApp }
  | { kind: "delete" };

function megabytes(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

/** D3 guarded .deb path: a downloaded .deb is inspected, and supported
 * app packages install into the dedicated rowyn-deb container (never
 * the immutable host) and are exported to the menu. System packages
 * are refused by the inspector with their reasons. Flatpak stays the
 * recommended route and is named first wherever an equivalent exists. */
export function RowynDebCard() {
  const [state, setState] = useState<DebState | null>(null);
  const [report, setReport] = useState<DebInspectReport | null>(null);
  const [job, setJob] = useState<DebJob | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [message, setMessage] = useState("Reading the app container…");
  const controllerRef = useRef<DebJobController | null>(null);

  const refresh = async () => {
    const next = await rowynDebState();
    setState(next);
    return next;
  };

  useEffect(() => {
    let live = true;
    const controller = createJobController(productionDebClient, {
      onJob: (next) => {
        if (!live) return;
        setJob(next);
        if (next.status !== "running") {
          setPendingAction(null);
          void refresh().catch(() => undefined);
        }
      },
      onError: (error) => {
        if (!live) return;
        setMessage(error instanceof Error ? error.message : String(error));
      },
    });
    controllerRef.current = controller;
    void refresh()
      .then((next) => {
        if (!live) return;
        setMessage(describeState(next.container));
        if (next.pending) {
          return rowynDebInspect(next.pending).then((inspected) => {
            if (live) setReport(inspected);
          });
        }
        return undefined;
      })
      .catch((error: unknown) => {
        if (live) setMessage(error instanceof Error ? error.message : String(error));
      });
    return () => {
      live = false;
      controller.dispose();
    };
  }, []);

  const running = job?.status === "running";
  const pendingName = state?.pending?.split("/").pop() ?? null;
  const launchable = report !== null
    && (report.desktop_files.length > 0 || report.binaries.length > 0);

  const confirmAction = () => {
    const controller = controllerRef.current;
    if (!controller || !pendingAction) return;
    if (pendingAction.kind === "install" && report && state?.pending) {
      void controller.start("install", installConfirmation(report), state.pending);
    } else if (pendingAction.kind === "remove") {
      void controller.start(removalOperation(pendingAction.app), removalConfirmation(pendingAction.app));
    } else if (pendingAction.kind === "delete") {
      void controller.start("delete", deleteConfirmation());
    }
    setPendingAction(null);
  };

  return <section className="glass dashboard-card">
    <h3>Install a downloaded app (.deb)</h3>
    <p className="card-copy">
      Apps from the store above (Flatpak) come first — they update themselves and never
      touch the system. When a program only offers a .deb download, Rowyn can install it
      into a dedicated <strong>app container</strong> instead of the system and add it to
      your menu like any other app. Packages that are really part of an operating system —
      drivers, kernel modules, background services — cannot work in a container and are
      refused, with the reason.
    </p>
    <p className="card-copy" role="status">{message}</p>

    {state?.container.state === "absent" && <div className="home-actions">
      <button className="action-button" type="button" disabled={running}
        onClick={() => void controllerRef.current?.start("create")}>
        Create the app container (one-time download)
      </button>
    </div>}

    {state?.pending && <div>
      <hr />
      <h4>Downloaded package: {pendingName}</h4>
      {report && <>
        <p className="card-copy">
          <strong>{report.package}</strong> {report.version} ({report.architecture}) — {report.description}
        </p>
        <p className="card-copy">
          From: {report.maintainer}. Download: {megabytes(report.file_size_bytes)};
          installed size about {megabytes(report.installed_size_kib * 1024)}.
        </p>
        <p className="card-copy">{signatureText(report)}</p>
        {report.flatpak_hint && <p className="card-copy">
          This app is also in the store as a Flatpak — <strong>{report.flatpak_hint.label}</strong>
          {" "}({report.flatpak_hint.id}). Installing it from the catalog above is the
          recommended route; the .deb below is the fallback.
        </p>}
        {report.notes.map((note) => <p className="card-copy" key={note.signal}>{note.text}</p>)}
        {report.verdict === "system" && <div>
          <p className="card-copy">
            <strong>Rowyn cannot install this package.</strong> It is a system package:
          </p>
          {report.reasons.map((reason) => <p className="card-copy" key={reason.signal}>{reason.text}</p>)}
          <p className="card-copy">
            Packages like this belong in the Rowyn system image itself. If a Flatpak
            version of the app exists, install that from the catalog above instead.
          </p>
        </div>}
        {report.verdict === "user-app" && !launchable && <p className="card-copy">
          This package contains no application or command Rowyn could add to your menu
          (it may be fonts or data for another program), so there is nothing to install.
        </p>}
        {report.verdict === "user-app" && launchable && <div className="home-actions">
          <button className="action-button" type="button" disabled={running}
            onClick={() => setPendingAction({ kind: "install" })}>
            Install {report.package} in the app container
          </button>
        </div>}
      </>}
    </div>}

    {state && state.apps.length > 0 && <div>
      <hr />
      <h4>Apps installed from .deb files</h4>
      <p className="card-copy">
        These run in the app container (rowyn-deb), not in the system. Removing one
        takes it out of the container and out of your menu.
      </p>
      <ul>
        {state.apps.map((app) => <li key={app.package}>
          <strong>{app.package}</strong> {app.version} · App container (.deb) · from {app.source}
          {" "}
          <button className="action-button" type="button" disabled={running}
            onClick={() => setPendingAction({ kind: "remove", app })}>
            Remove
          </button>
        </li>)}
      </ul>
    </div>}

    {state?.container.state === "managed" && <div className="home-actions">
      <button className="action-button danger" type="button" disabled={running}
        onClick={() => setPendingAction({ kind: "delete" })}>
        Delete the app container
      </button>
    </div>}

    {pendingAction && <section role="alertdialog" aria-modal="false" aria-label="Confirm app container action">
      <hr />
      {pendingAction.kind === "install" && report && <>
        <h4>Install {report.package} {report.version}?</h4>
        <p className="card-copy">
          {signatureText(report)} The app will be installed into the rowyn-deb app
          container and added to your menu. It will not be installed into the system.
        </p>
      </>}
      {pendingAction.kind === "remove" && <>
        <h4>Remove {pendingAction.app.package}?</h4>
        <p className="card-copy">
          {pendingAction.app.package} {pendingAction.app.version} will be removed from
          the app container and from your menu. Files you created with it stay in your
          home folder.
        </p>
      </>}
      {pendingAction.kind === "delete" && <>
        <h4>Delete the app container?</h4>
        <p className="card-copy">
          Every app installed from a .deb file will be removed, along with its menu
          entries. Your own files in your home folder are kept. You can create the
          container again later (that re-downloads the environment).
        </p>
      </>}
      <div className="home-actions">
        <button className="action-button danger" type="button" onClick={confirmAction}>Confirm</button>
        <button className="action-button" type="button" onClick={() => setPendingAction(null)}>Cancel</button>
      </div>
    </section>}

    {job && <div>
      <hr />
      <p className="card-copy" role="status">
        <strong>{job.status}</strong> — {job.detail}
      </p>
      {running && <div className="home-actions">
        <button className="action-button" type="button"
          onClick={() => void controllerRef.current?.cancel()}>
          Cancel operation
        </button>
      </div>}
    </div>}
  </section>;
}
