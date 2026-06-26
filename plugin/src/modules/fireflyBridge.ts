const BRIDGE_HOST = "127.0.0.1";
const BRIDGE_PORT = 8765;
const BRIDGE_HEALTH_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/health`;
const BRIDGE_MESSAGE_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/message`;
const BRIDGE_STREAM_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/stream`;
const BRIDGE_HISTORY_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/history`;
const BRIDGE_META_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/meta`;
const BRIDGE_CANCEL_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/cancel`;
const BRIDGE_SESSION_CLEAR_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/session/clear`;
const BRIDGE_PDF_OPENED_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/pdf-opened`;
const BRIDGE_CONVERT_MARKDOWN_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/convert-markdown`;
const BRIDGE_WIKI_INGEST_MARKDOWN_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/wiki-ingest-markdown`;
const BRIDGE_DELETE_TURN_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/session/delete-turn`;
const BRIDGE_SETTINGS_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/settings`;

export type SettingsFileKey = "config" | "context" | "user";

export interface SettingsFilePayload {
  path: string;
  exists: boolean;
  content: string;
  error?: string;
}

export interface SaveSettingsResult {
  ok: boolean;
  saved?: string[];
  errors?: Record<string, string>;
  restart_recommended?: boolean;
}

const WIKI_INGEST_MARKDOWN_TIMEOUT_MS = 600_000;
const CONVERT_MARKDOWN_TIMEOUT_MS = 600_000;

const HEALTH_RETRY = 40;
const HEALTH_INTERVAL_MS = 500;
const HEALTH_TIMEOUT_MS = 2000;
const SEND_TIMEOUT_MS = 15000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function zoteroHttpRequest(
  method: "GET" | "POST" | "PUT",
  url: string,
  options: {
    headers?: Record<string, string>;
    body?: string;
    timeout?: number;
  } = {},
): Promise<{ status: number; responseText: string }> {
  const z: any = (globalThis as any).Zotero;
  if (!z?.HTTP?.request) {
    throw new Error("Zotero.HTTP.request is unavailable");
  }
  const resp = await z.HTTP.request(method, url, {
    responseType: "text",
    timeout: options.timeout ?? 15000,
    headers: options.headers ?? {},
    body: options.body,
  });
  return {
    status: Number(resp?.status ?? 0),
    responseText: String(resp?.responseText ?? ""),
  };
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  externalSignal?: AbortSignal,
): Promise<Response> {
  const AC: any = (globalThis as any).AbortController;

  // Zotero 的运行环境在部分版本里没有 AbortController，需做兼容降级。
  if (!AC) {
    if (externalSignal?.aborted) {
      throw new Error("Request aborted");
    }
    return (await Promise.race([
      fetch(url, init),
      new Promise<Response>((_, reject) => {
        externalSignal?.addEventListener("abort", () => reject(new Error("Request aborted")), {
          once: true,
        });
      }),
      new Promise<Response>((_, reject) =>
        setTimeout(() => reject(new Error(`Request timeout after ${timeoutMs}ms`)), timeoutMs),
      ),
    ])) as Response;
  }

  const controller: any = new AC();
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
    }
  }
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function isBridgeHealthy(): Promise<boolean> {
  try {
    const res = await zoteroHttpRequest("GET", BRIDGE_HEALTH_URL, {
      timeout: HEALTH_TIMEOUT_MS,
    });
    return res.status >= 200 && res.status < 300;
  } catch (_e1) {
    try {
      const res = await fetchWithTimeout(BRIDGE_HEALTH_URL, { method: "GET" }, HEALTH_TIMEOUT_MS);
      return res.ok;
    } catch (_e2) {
      return false;
    }
  }
}

export async function ensureFireFlyBridgeStarted(): Promise<void> {
  if (await isBridgeHealthy()) return;
  for (let i = 0; i < HEALTH_RETRY; i++) {
    if (await isBridgeHealthy()) {
      return;
    }
    await sleep(HEALTH_INTERVAL_MS);
  }
  throw new Error(
    "FireFly bridge not ready. Start the backend: run release/start-bridge.ps1 (Release) or python backend/FireFly/scripts/zotero_bridge_launcher.py (dev). See INSTALL.md.",
  );
}

export async function deleteZoteroConversationTurn(
  sessionID: string,
  assistantMessageIndex: number,
): Promise<{ ok: boolean; detail?: string }> {
  const body = JSON.stringify({
    session_id: sessionID,
    assistant_message_index: assistantMessageIndex,
  });
  try {
    const resp = await zoteroHttpRequest("POST", BRIDGE_DELETE_TURN_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: SEND_TIMEOUT_MS,
    });
    if (resp.status < 200 || resp.status >= 300) {
      throw new Error(`Bridge delete-turn failed: ${resp.status} ${resp.responseText}`);
    }
    const data = JSON.parse(resp.responseText || "{}") as unknown as { ok?: boolean; detail?: string };
    return { ok: Boolean(data.ok), detail: data.detail != null ? String(data.detail) : undefined };
  } catch (_e1) {
    const res = await fetchWithTimeout(
      BRIDGE_DELETE_TURN_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      },
      SEND_TIMEOUT_MS,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Bridge delete-turn failed: ${res.status} ${text}`);
    }
    const data = (await res.json()) as unknown as { ok?: boolean; detail?: string };
    return { ok: Boolean(data.ok), detail: data.detail != null ? String(data.detail) : undefined };
  }
}

export async function sendToFireFly(message: string, sessionID = "cli:direct") {
  const body = JSON.stringify({
    message,
    session_id: sessionID,
  });
  try {
    const resp = await zoteroHttpRequest("POST", BRIDGE_MESSAGE_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: SEND_TIMEOUT_MS,
    });
    if (resp.status < 200 || resp.status >= 300) {
      throw new Error(`Bridge request failed: ${resp.status} ${resp.responseText}`);
    }
    return JSON.parse(resp.responseText || "{}");
  } catch (_e1) {
    const res = await fetchWithTimeout(
      BRIDGE_MESSAGE_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      },
      SEND_TIMEOUT_MS,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Bridge request failed: ${res.status} ${text}`);
    }
    return res.json();
  }
}

export async function streamFromFireFly(
  message: string,
  sessionID: string,
  mediaPaths: string[] = [],
  onDelta: (delta: string) => void,
  onFinal?: (content: string) => void,
  onThinkingDelta?: (delta: string) => void,
  onToolStep?: (content: string) => void,
  onStreamEnd?: (resuming: boolean) => void,
  abortSignal?: AbortSignal,
  literatureTitle?: string,
): Promise<void> {
  const payload: Record<string, unknown> = {
    message,
    session_id: sessionID,
    media: Array.isArray(mediaPaths) ? mediaPaths : [],
  };
  const lit = String(literatureTitle || "").trim();
  if (lit) {
    payload.literature_title = lit;
  }
  const body = JSON.stringify(payload);
  const res = await fetchWithTimeout(
    BRIDGE_STREAM_URL,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    },
    SEND_TIMEOUT_MS * 4,
    abortSignal,
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Bridge stream failed: ${res.status} ${text}`);
  }
  if (!res.body) {
    throw new Error("Bridge stream has no response body");
  }

  const reader: any = (res.body as any).getReader({});
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sepIdx = buffer.indexOf("\n\n");
    while (sepIdx >= 0) {
      const block = buffer.slice(0, sepIdx).trim();
      buffer = buffer.slice(sepIdx + 2);
      if (block.startsWith("data:")) {
        const raw = block.slice(5).trim();
        try {
          const event = JSON.parse(raw) as {
            type?: string;
            delta?: string;
            content?: string;
            message?: string;
            resuming?: string | boolean;
          };
          if (event.type === "delta" && event.delta) {
            onDelta(event.delta);
          } else if (event.type === "thinking_delta" && event.delta) {
            onThinkingDelta?.(event.delta);
          } else if (event.type === "tool_step" && event.content) {
            onToolStep?.(event.content);
          } else if (event.type === "end") {
            const resuming = event.resuming === true || event.resuming === "true";
            onStreamEnd?.(resuming);
          } else if (event.type === "final" && event.content) {
            onFinal?.(event.content);
          } else if (event.type === "error") {
            throw new Error(event.message || "unknown stream error");
          }
        } catch (e) {
          throw new Error(`Invalid stream event: ${String(e)}`);
        }
      }
      sepIdx = buffer.indexOf("\n\n");
    }
  }
}

export type ZoteroHistoryRow = {
  role: string;
  content: string;
  reasoning_content?: string;
  literature_title?: string;
  session_message_index?: number;
  /** ISO-like string from session JSONL ``timestamp`` */
  timestamp?: string;
};

export const fetchZoteroChatHistories = async (sessionID?: string): Promise<Record<string, ZoteroHistoryRow[]>> => {
  const query = sessionID ? `?session_id=${encodeURIComponent(sessionID)}` : "";
  const url = `${BRIDGE_HISTORY_URL}${query}`;
  const tryParse = (raw: string) => {
    const parsed = JSON.parse(raw || "{}") as { sessions?: Record<string, ZoteroHistoryRow[]> };
    return parsed.sessions ?? {};
  };
  try {
    const res = await zoteroHttpRequest("GET", url, { timeout: SEND_TIMEOUT_MS });
    if (res.status < 200 || res.status >= 300) {
      throw new Error(`Bridge history failed: ${res.status} ${res.responseText}`);
    }
    return tryParse(res.responseText);
  } catch (_e1) {
    const res = await fetchWithTimeout(url, { method: "GET" }, SEND_TIMEOUT_MS);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Bridge history failed: ${res.status} ${text}`);
    }
    const parsed = (await res.json()) as { sessions?: Record<string, ZoteroHistoryRow[]> };
    return parsed.sessions ?? {};
  }
};

export async function fetchBridgeMeta(): Promise<{ model?: string; provider?: string }> {
  try {
    const res = await zoteroHttpRequest("GET", BRIDGE_META_URL, { timeout: SEND_TIMEOUT_MS });
    if (res.status < 200 || res.status >= 300) {
      throw new Error(`Bridge meta failed: ${res.status} ${res.responseText}`);
    }
    const parsed = JSON.parse(res.responseText || "{}") as { model?: string; provider?: string };
    return {
      model: String(parsed.model || "").trim(),
      provider: String(parsed.provider || "").trim(),
    };
  } catch (_e1) {
    const res = await fetchWithTimeout(BRIDGE_META_URL, { method: "GET" }, SEND_TIMEOUT_MS);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Bridge meta failed: ${res.status} ${text}`);
    }
    const parsed = (await res.json()) as { model?: string; provider?: string };
    return {
      model: String(parsed.model || "").trim(),
      provider: String(parsed.provider || "").trim(),
    };
  }
}

export async function clearZoteroSession(sessionID: string): Promise<{ ok: boolean; session_id?: string; error?: string }> {
  const body = JSON.stringify({
    session_id: String(sessionID || "").trim(),
  });
  try {
    const resp = await zoteroHttpRequest("POST", BRIDGE_SESSION_CLEAR_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: SEND_TIMEOUT_MS,
    });
    const parsed = JSON.parse(resp.responseText || "{}") as {
      ok?: boolean;
      session_id?: string;
      error?: string;
    };
    if (resp.status < 200 || resp.status >= 300) {
      return {
        ok: false,
        error: String(parsed.error || resp.responseText || `HTTP ${resp.status}`),
      };
    }
    return {
      ok: !!parsed.ok,
      session_id: String(parsed.session_id || "").trim() || undefined,
      error: parsed.error ? String(parsed.error) : undefined,
    };
  } catch (_e1) {
    const res = await fetchWithTimeout(
      BRIDGE_SESSION_CLEAR_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      },
      SEND_TIMEOUT_MS,
    );
    const parsed = (await res.json()) as { ok?: boolean; session_id?: string; error?: string };
    if (!res.ok) {
      return {
        ok: false,
        error: String(parsed.error || `HTTP ${res.status}`),
      };
    }
    return {
      ok: !!parsed.ok,
      session_id: String(parsed.session_id || "").trim() || undefined,
    };
  }
}

export async function cancelFireFlyStream(sessionID: string): Promise<{ ok: boolean; detail?: string }> {
  const body = JSON.stringify({
    session_id: String(sessionID || "").trim(),
  });
  try {
    const resp = await zoteroHttpRequest("POST", BRIDGE_CANCEL_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: SEND_TIMEOUT_MS,
    });
    if (resp.status < 200 || resp.status >= 300) {
      throw new Error(`Bridge cancel failed: ${resp.status} ${resp.responseText}`);
    }
    const parsed = JSON.parse(resp.responseText || "{}") as { ok?: boolean; detail?: string };
    return { ok: !!parsed.ok, detail: String(parsed.detail || "").trim() || undefined };
  } catch (_e1) {
    const res = await fetchWithTimeout(
      BRIDGE_CANCEL_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      },
      SEND_TIMEOUT_MS,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Bridge cancel failed: ${res.status} ${text}`);
    }
    const parsed = (await res.json()) as { ok?: boolean; detail?: string };
    return { ok: !!parsed.ok, detail: String(parsed.detail || "").trim() || undefined };
  }
}

// Backward-compatible aliases (to be removed after all call sites migrate).
export const ensureNanobotBridgeStarted = ensureFireFlyBridgeStarted;
export const sendToNanobot = sendToFireFly;
export const streamFromNanobot = streamFromFireFly;
export const cancelNanobotStream = cancelFireFlyStream;

export async function notifyZoteroPdfOpened(payload: {
  pdf_path?: string;
  pdf_dir?: string;
  pdf_name?: string;
  wiki_pdf_path?: string;
}) {
  const body = JSON.stringify({
    pdf_path: String(payload.pdf_path || "").trim(),
    pdf_dir: String(payload.pdf_dir || "").trim(),
    pdf_name: String(payload.pdf_name || "").trim(),
    wiki_pdf_path: String(payload.wiki_pdf_path || "").trim(),
  });
  try {
    const resp = await zoteroHttpRequest("POST", BRIDGE_PDF_OPENED_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: SEND_TIMEOUT_MS * 2,
    });
    if (resp.status < 200 || resp.status >= 300) {
      throw new Error(`Bridge pdf-opened failed: ${resp.status} ${resp.responseText}`);
    }
    return JSON.parse(resp.responseText || "{}");
  } catch (_e1) {
    const res = await fetchWithTimeout(
      BRIDGE_PDF_OPENED_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      },
      SEND_TIMEOUT_MS * 2,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Bridge pdf-opened failed: ${res.status} ${text}`);
    }
    return res.json();
  }
}

export type WikiIngestMarkdownResult = {
  ok: boolean;
  detail: string;
  markdown_path?: string | null;
  wiki_path?: string | null;
  source_truncated?: boolean;
};

/** 根据当前文献在 raw/markdown 下的 .md，调用 FireFly LLM 生成/覆盖镜像 wiki 页（与 convert 相同 pdf 字段）。 */
export async function triggerWikiIngestFromMarkdown(payload: {
  pdf_path?: string;
  pdf_dir?: string;
  pdf_name?: string;
  wiki_pdf_path?: string;
}): Promise<WikiIngestMarkdownResult> {
  const body = JSON.stringify({
    pdf_path: String(payload.pdf_path || "").trim(),
    pdf_dir: String(payload.pdf_dir || "").trim(),
    pdf_name: String(payload.pdf_name || "").trim(),
    wiki_pdf_path: String(payload.wiki_pdf_path || "").trim(),
  });
  try {
    const resp = await zoteroHttpRequest("POST", BRIDGE_WIKI_INGEST_MARKDOWN_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: WIKI_INGEST_MARKDOWN_TIMEOUT_MS,
    });
    if (resp.status < 200 || resp.status >= 300) {
      throw new Error(`Bridge wiki-ingest failed: ${resp.status} ${resp.responseText}`);
    }
    const data = JSON.parse(resp.responseText || "{}") as unknown as WikiIngestMarkdownResult;
    return {
      ok: Boolean(data.ok),
      detail: String(data.detail || ""),
      markdown_path: data.markdown_path != null ? String(data.markdown_path) : null,
      wiki_path: data.wiki_path != null ? String(data.wiki_path) : null,
      source_truncated: Boolean(data.source_truncated),
    };
  } catch (_e1) {
    const res = await fetchWithTimeout(
      BRIDGE_WIKI_INGEST_MARKDOWN_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      },
      WIKI_INGEST_MARKDOWN_TIMEOUT_MS,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Wiki ingest failed: ${res.status} ${text}`);
    }
    const data = (await res.json()) as unknown as WikiIngestMarkdownResult;
    return {
      ok: Boolean(data.ok),
      detail: String(data.detail || ""),
      markdown_path: data.markdown_path != null ? String(data.markdown_path) : null,
      wiki_path: data.wiki_path != null ? String(data.wiki_path) : null,
      source_truncated: Boolean(data.source_truncated),
    };
  }
}

export async function convertCurrentPdfToMarkdown(payload: {
  pdf_path?: string;
  pdf_dir?: string;
  pdf_name?: string;
  wiki_pdf_path?: string;
  /** 默认 true：即便已有 RAG，也按当前 Markdown 重新切片并覆盖 jsonl */
  rebuild_rag?: boolean;
  /** 仅高级用途：为 true 时从 PDF 覆盖已有 .md；默认 false（有 .md 则只重建 RAG） */
  force_markdown?: boolean;
  /** 默认 true：导出图片到 `<stem>.assets/` */
  write_images?: boolean;
}) {
  const body = JSON.stringify({
    pdf_path: String(payload.pdf_path || "").trim(),
    pdf_dir: String(payload.pdf_dir || "").trim(),
    pdf_name: String(payload.pdf_name || "").trim(),
    wiki_pdf_path: String(payload.wiki_pdf_path || "").trim(),
    rebuild_rag: payload.rebuild_rag !== false,
    force_markdown: payload.force_markdown === true,
    write_images: payload.write_images !== false,
  });
  try {
    const resp = await zoteroHttpRequest("POST", BRIDGE_CONVERT_MARKDOWN_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: CONVERT_MARKDOWN_TIMEOUT_MS,
    });
    if (resp.status < 200 || resp.status >= 300) {
      throw new Error(
        `Bridge convert-markdown failed: ${resp.status} ${formatBridgeErrorBody(resp.responseText)}`,
      );
    }
    return JSON.parse(resp.responseText || "{}");
  } catch (_e1) {
    const res = await fetchWithTimeout(
      BRIDGE_CONVERT_MARKDOWN_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      },
      CONVERT_MARKDOWN_TIMEOUT_MS,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(
        `Bridge convert-markdown failed: ${res.status} ${formatBridgeErrorBody(text)}`,
      );
    }
    return res.json();
  }
}

function formatBridgeErrorBody(raw: string): string {
  const text = String(raw || "").trim();
  if (!text) return "(empty response)";
  try {
    const parsed = JSON.parse(text) as { error?: string; error_log?: string | null };
    const parts: string[] = [];
    if (parsed.error) parts.push(String(parsed.error));
    if (parsed.error_log) parts.push(`详细日志: ${parsed.error_log}`);
    return parts.length ? parts.join("\n") : text;
  } catch {
    return text;
  }
}

export async function fetchBridgeSettings(): Promise<
  Record<SettingsFileKey, SettingsFilePayload>
> {
  const res = await zoteroHttpRequest("GET", BRIDGE_SETTINGS_URL, { timeout: 15000 });
  if (res.status < 200 || res.status >= 300) {
    throw new Error(
      `Bridge settings GET failed: ${res.status} ${formatBridgeErrorBody(res.responseText)}`,
    );
  }
  const parsed = JSON.parse(res.responseText) as {
    ok?: boolean;
    files?: Record<string, SettingsFilePayload>;
  };
  const files = parsed.files || {};
  return {
    config: files.config || { path: "", exists: false, content: "" },
    context: files.context || { path: "", exists: false, content: "" },
    user: files.user || { path: "", exists: false, content: "" },
  };
}

export async function saveBridgeSettings(
  payload: Partial<Record<SettingsFileKey, string>>,
): Promise<SaveSettingsResult> {
  const body = JSON.stringify(payload);
  let res = await zoteroHttpRequest("PUT", BRIDGE_SETTINGS_URL, {
    headers: { "Content-Type": "application/json" },
    body,
    timeout: 30000,
  });
  if (res.status === 405 || res.status === 501) {
    res = await zoteroHttpRequest("POST", BRIDGE_SETTINGS_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: 30000,
    });
  }
  let parsed: SaveSettingsResult;
  try {
    parsed = JSON.parse(res.responseText) as SaveSettingsResult;
  } catch {
    throw new Error(
      `Bridge settings save failed: ${res.status} ${formatBridgeErrorBody(res.responseText)}`,
    );
  }
  if (res.status < 200 || res.status >= 300) {
    return {
      ok: false,
      errors: parsed.errors || { _http: `HTTP ${res.status}` },
    };
  }
  return parsed;
}

