import { invoke } from "@tauri-apps/api/core";
import type {
  DebClient,
  DebInspectReport,
  DebJob,
  DebOperation,
  DebState,
} from "./rowynDebModel";

export async function rowynDebState(): Promise<DebState> {
  return invoke<DebState>("rowyn_deb_state");
}

export async function rowynDebInspect(path: string): Promise<DebInspectReport> {
  return invoke<DebInspectReport>("rowyn_deb_inspect", { path });
}

export async function rowynDebStart(
  operation: DebOperation,
  confirmation?: string,
  path?: string,
): Promise<DebJob> {
  return invoke<DebJob>("rowyn_deb_start", {
    operation,
    confirmation: confirmation ?? null,
    path: path ?? null,
  });
}

export async function rowynDebJob(id: string): Promise<DebJob> {
  return invoke<DebJob>("rowyn_deb_job", { id });
}

export async function rowynDebCancel(id: string): Promise<DebJob> {
  return invoke<DebJob>("rowyn_deb_cancel", { id });
}

export const productionDebClient: DebClient = {
  start: rowynDebStart,
  job: rowynDebJob,
  cancel: rowynDebCancel,
};
