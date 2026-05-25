import { config } from "../../package.json";
import { getString } from "../utils/locale";
import {
  fetchBridgeSettings,
  isBridgeHealthy,
  saveBridgeSettings,
  type SettingsFileKey,
} from "./fireflyBridge";

const SETTING_KEYS: SettingsFileKey[] = ["config", "context", "user"];
const boundPrefsWindows = new WeakSet<Window>();

export async function registerPrefsScripts(_window: Window) {
  if (!addon.data.prefs) {
    addon.data.prefs = { window: _window };
  } else {
    addon.data.prefs.window = _window;
  }
  bindPrefEvents();
  await loadSettingsIntoUI();
}

function prefId(suffix: string): string {
  return `${config.addonRef}-${suffix}`;
}

function getDoc(): Document {
  return addon.data.prefs!.window.document;
}

function setStatus(message: string, isError = false) {
  const el = getDoc().getElementById(prefId("pref-status"));
  if (!el) return;
  el.textContent = message;
  el.setAttribute(
    "style",
    `margin-bottom: 8px; color: ${isError ? "var(--color-red)" : "var(--fill-secondary)"}`,
  );
}

function setPathLabel(key: SettingsFileKey, path: string, exists: boolean) {
  const el = getDoc().getElementById(prefId(`path-${key}`));
  if (!el) return;
  const suffix = exists ? "" : ` (${getString("pref-status-new-file") || "new"})`;
  el.textContent = path + suffix;
}

function getEditor(key: SettingsFileKey): HTMLTextAreaElement | null {
  return getDoc().getElementById(prefId(`editor-${key}`)) as HTMLTextAreaElement | null;
}

function collectEditors(): Partial<Record<SettingsFileKey, string>> {
  const out: Partial<Record<SettingsFileKey, string>> = {};
  for (const key of SETTING_KEYS) {
    const editor = getEditor(key);
    if (editor) out[key] = editor.value;
  }
  return out;
}

function validateLocalJson(payload: Partial<Record<SettingsFileKey, string>>): string | null {
  for (const key of SETTING_KEYS) {
    const raw = payload[key];
    if (raw == null) continue;
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return `${key}.json: root must be an object`;
      }
    } catch (e) {
      return `${key}.json: ${String(e)}`;
    }
  }
  return null;
}

async function loadSettingsIntoUI() {
  setStatus(getString("pref-status-loading"));
  for (const key of SETTING_KEYS) {
    const editor = getEditor(key);
    if (editor) editor.value = "";
    setPathLabel(key, "—", false);
  }

  if (!(await isBridgeHealthy())) {
    setStatus(getString("pref-status-bridge-offline"), true);
    return;
  }

  try {
    const files = await fetchBridgeSettings();
    for (const key of SETTING_KEYS) {
      const entry = files[key];
      const editor = getEditor(key);
      if (editor) {
        editor.value = entry.content || (entry.exists ? "" : "{\n}\n");
      }
      setPathLabel(key, entry.path, entry.exists);
      if (entry.error) {
        ztoolkit.log(`settings load warning (${key}):`, entry.error);
      }
    }
    setStatus(getString("pref-status-loaded"));
  } catch (e) {
    setStatus(`${getString("pref-status-save-failed")}: ${String(e)}`, true);
    Zotero.logError(e instanceof Error ? e : new Error(String(e)));
  }
}

async function saveSettingsFromUI() {
  const payload = collectEditors();
  const localErr = validateLocalJson(payload);
  if (localErr) {
    setStatus(`${getString("pref-status-invalid-json")}: ${localErr}`, true);
    return;
  }

  if (!(await isBridgeHealthy())) {
    setStatus(getString("pref-status-bridge-offline"), true);
    return;
  }

  setStatus(getString("pref-status-loading"));
  try {
    const result = await saveBridgeSettings(payload);
    if (!result.ok) {
      const detail = Object.entries(result.errors || {})
        .map(([k, v]) => `${k}: ${v}`)
        .join("\n");
      setStatus(`${getString("pref-status-save-failed")}\n${detail}`, true);
      return;
    }
    let msg = getString("pref-status-saved");
    if (result.restart_recommended) {
      msg += `\n${getString("pref-restart-hint")}`;
    }
    setStatus(msg);
    await loadSettingsIntoUI();
  } catch (e) {
    setStatus(`${getString("pref-status-save-failed")}: ${String(e)}`, true);
    Zotero.logError(e instanceof Error ? e : new Error(String(e)));
  }
}

function bindPrefEvents() {
  const win = addon.data.prefs!.window;
  if (boundPrefsWindows.has(win)) return;

  const reloadBtn = win.document.getElementById(prefId("pref-reload"));
  reloadBtn?.addEventListener("command", () => {
    void loadSettingsIntoUI();
  });
  if (reloadBtn) reloadBtn.setAttribute("label", getString("pref-reload"));

  const saveBtn = win.document.getElementById(prefId("pref-save"));
  saveBtn?.addEventListener("command", () => {
    void saveSettingsFromUI();
  });
  if (saveBtn) saveBtn.setAttribute("label", getString("pref-save"));

  for (const key of SETTING_KEYS) {
    const tab = win.document.getElementById(prefId(`tab-${key}`));
    if (tab) tab.setAttribute("label", getString(`pref-tab-${key}`));
  }

  boundPrefsWindows.add(win);
}
