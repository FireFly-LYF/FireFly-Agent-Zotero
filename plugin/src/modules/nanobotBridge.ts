const BRIDGE_HOST = "127.0.0.1";
const BRIDGE_PORT = 8765;
const BRIDGE_HEALTH_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/health`;
const BRIDGE_MESSAGE_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/message`;
const BRIDGE_STREAM_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/stream`;
const BRIDGE_HISTORY_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/history`;
const BRIDGE_PDF_OPENED_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/pdf-opened`;
const BRIDGE_CONVERT_MARKDOWN_URL = `http://${BRIDGE_HOST}:${BRIDGE_PORT}/zotero/convert-markdown`;

const HEALTH_RETRY = 40;
const HEALTH_INTERVAL_MS = 500;
const HEALTH_TIMEOUT_MS = 2000;
const SEND_TIMEOUT_MS = 15000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function zoteroHttpRequest(
  method: "GET" | "POST",
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

export async function ensureNanobotBridgeStarted(): Promise<void> {
  if (await isBridgeHealthy()) return;
  for (let i = 0; i < HEALTH_RETRY; i++) {
    if (await isBridgeHealthy()) {
      return;
    }
    await sleep(HEALTH_INTERVAL_MS);
  }
  throw new Error(
    "Nanobot bridge not ready. Please run backend/nanobot/scripts/zotero_bridge_launcher.py manually.",
  );
}

export async function sendToNanobot(message: string, sessionID = "cli:direct") {
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

export async function streamFromNanobot(
  message: string,
  sessionID: string,
  thinkingState: "Enable" | "Disable",
  mediaPaths: string[] = [],
  onDelta: (delta: string) => void,
  onFinal?: (content: string) => void,
  onThinkingDelta?: (delta: string) => void,
  abortSignal?: AbortSignal,
): Promise<void> {
  const body = JSON.stringify({
    message,
    session_id: sessionID,
    thinking_state: thinkingState,
    media: Array.isArray(mediaPaths) ? mediaPaths : [],
  });
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
          };
          if (event.type === "delta" && event.delta) {
            onDelta(event.delta);
          } else if (event.type === "thinking_delta" && event.delta) {
            onThinkingDelta?.(event.delta);
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

export const fetchZoteroChatHistories = async (sessionID?: string): Promise<
  Record<string, Array<{ role: string; content: string; reasoning_content?: string }>>
> => {
  const query = sessionID ? `?session_id=${encodeURIComponent(sessionID)}` : "";
  const url = `${BRIDGE_HISTORY_URL}${query}`;
  const tryParse = (raw: string) => {
    const parsed = JSON.parse(raw || "{}") as {
      sessions?: Record<string, Array<{ role: string; content: string; reasoning_content?: string }>>;
    };
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
    const parsed = (await res.json()) as {
      sessions?: Record<string, Array<{ role: string; content: string; reasoning_content?: string }>>;
    };
    return parsed.sessions ?? {};
  }
};

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

export async function convertCurrentPdfToMarkdown(payload: {
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
    const resp = await zoteroHttpRequest("POST", BRIDGE_CONVERT_MARKDOWN_URL, {
      headers: { "Content-Type": "application/json" },
      body,
      timeout: SEND_TIMEOUT_MS * 8,
    });
    if (resp.status < 200 || resp.status >= 300) {
      throw new Error(`Bridge convert-markdown failed: ${resp.status} ${resp.responseText}`);
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
      SEND_TIMEOUT_MS * 8,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Bridge convert-markdown failed: ${res.status} ${text}`);
    }
    return res.json();
  }
}

