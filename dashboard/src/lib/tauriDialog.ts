// Thin wrapper around the Tauri dialog plugin (exposed globally via
// withGlobalTauri). In the desktop app these open native file pickers; in a
// plain browser dev session there is no __TAURI__, so callers should fall back
// to a manual path text input (see isNativeDialogAvailable).

type DialogOpenOptions = {
  title?: string;
  defaultPath?: string;
  directory?: boolean;
  multiple?: boolean;
  filters?: { name: string; extensions: string[] }[];
};

type DialogSaveOptions = {
  title?: string;
  defaultPath?: string;
  filters?: { name: string; extensions: string[] }[];
};

type DialogApi = {
  open?: (opts?: DialogOpenOptions) => Promise<string | string[] | null>;
  save?: (opts?: DialogSaveOptions) => Promise<string | null>;
};

function getDialogApi(): DialogApi | undefined {
  const maybeWindow = window as Window & { __TAURI__?: { dialog?: DialogApi } };
  return maybeWindow.__TAURI__?.dialog;
}

export function isNativeDialogAvailable(): boolean {
  const api = getDialogApi();
  return Boolean(api?.open && api?.save);
}

export async function openFile(
  opts?: DialogOpenOptions,
): Promise<string | null> {
  const api = getDialogApi();
  if (!api?.open) return null;
  const result = await api.open({ multiple: false, ...opts });
  if (Array.isArray(result)) return result[0] ?? null;
  return result ?? null;
}

export async function openDirectory(
  opts?: Omit<DialogOpenOptions, "directory" | "multiple" | "filters">,
): Promise<string | null> {
  const api = getDialogApi();
  if (!api?.open) return null;
  const result = await api.open({ multiple: false, directory: true, ...opts });
  if (Array.isArray(result)) return result[0] ?? null;
  return result ?? null;
}

export async function saveFile(
  opts?: DialogSaveOptions,
): Promise<string | null> {
  const api = getDialogApi();
  if (!api?.save) return null;
  return (await api.save(opts)) ?? null;
}
