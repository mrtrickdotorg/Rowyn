// B8: the mount harness entry. The test bundles this file with virtual
// modules for React's hook/jsx runtime, the Tauri bridge, liveData and the
// page's small UI dependencies, so the real Play component runs in Node.
import { Play } from '../src/pages/RowynPlay';
import { createMount } from 'rowyn:react';

export function mountPlay() {
  return createMount(Play);
}
