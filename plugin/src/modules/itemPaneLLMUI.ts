import { getLocaleID } from "../utils/locale";
import { renderMarkdown } from "../utils/markdown";
import {
  convertCurrentPdfToMarkdown,
  cancelFireFlyStream,
  clearZoteroSession,
  ensureFireFlyBridgeStarted,
  fetchBridgeMeta,
  deleteZoteroConversationTurn,
  fetchZoteroChatHistories,
  isBridgeHealthy,
  streamFromFireFly,
  triggerWikiIngestFromMarkdown,
} from "./fireflyBridge";
import { config } from "../../package.json";
import { ensureWikiPdfMirrorIfMissing, resolveWikiPdfInfo } from "./wikiPdfSync";

let bridgeHealthTimer: number | null = null;

/**
 * 独立的 Item Pane LLM 页面模块。
 * 负责将提问发送到本地 FireFly bridge（/zotero/message）。
 */
export function registerLLMItemPaneSection() {
  Zotero.ItemPaneManager.registerSection({
    paneID: "llm-ui",
    pluginID: addon.data.config.addonID,
    header: {
      l10nID: getLocaleID("item-section-example1-head-text"),
      icon: `chrome://${config.addonRef}/content/icons/firefly_smell.svg`,
    },
    sidenav: {
      l10nID: getLocaleID("item-section-example1-sidenav-tooltip"),
      icon: `chrome://${config.addonRef}/content/icons/firefly_smell.svg`,
    },
    onRender: ({ body, item }) => {
      const w = (body.ownerDocument?.defaultView ?? Zotero.getMainWindow()) as unknown as
        | Window
        | null
        | undefined;
      if (bridgeHealthTimer != null && w) {
        try {
          w.clearInterval(bridgeHealthTimer);
        } catch {
          // ignore
        }
        bridgeHealthTimer = null;
      }

      const doc = body.ownerDocument;
      if (!doc) return;
      const ownerDoc = doc;
      const bodyEl = body as HTMLElement;
      bodyEl.style.height = "calc(100vh - 160px)";
      bodyEl.style.minHeight = "680px";
      bodyEl.style.minWidth = "0";
      bodyEl.style.display = "flex";
      bodyEl.style.flexDirection = "column";

      const root = ownerDoc.createElement("div");
      root.style.display = "flex";
      root.style.flexDirection = "column";
      root.style.gap = "10px";
      root.style.padding = "10px 0";
      root.style.height = "100%";
      root.style.minWidth = "0";
      // Zotero pane 里部分容器默认不可选中，显式允许文本选择/复制。
      root.style.userSelect = "text";
      (root.style as any).MozUserSelect = "text";

      const topBar = ownerDoc.createElement("div");
      topBar.style.display = "flex";
      topBar.style.justifyContent = "space-between";
      topBar.style.alignItems = "center";
      topBar.style.gap = "8px";
      topBar.style.width = "100%";
      topBar.style.boxSizing = "border-box";

      type TabState = {
        id: number;
        chip: HTMLDivElement;
        labelBtn: HTMLButtonElement;
        closeBtn: HTMLButtonElement;
      };
      type ChatRecord = {
        role: "user" | "assistant";
        /** 后端 ``session.messages`` 中下标；仅 assistant 用于删除整轮对话。 */
        session_message_index?: number;
        /** 助手消息的 ISO 时间（用于底部展示），流式结束时写入 */
        turn_timestamp?: string;
        content: string;
        reasoning_content?: string;
        /** 发送时当前 Zotero 条目题名（与后端会话 JSONL 中 literature_title 一致） */
        literature_title?: string;
      };
      const chatTabs: TabState[] = [];
      const MAX_CHAT_TABS = 4;
      const chatHistoryByTab = new Map<number, ChatRecord[]>();
      let activeTabId = 1;
      const tabBar = ownerDoc.createElement("div");
      tabBar.style.display = "flex";
      tabBar.style.alignItems = "center";
      tabBar.style.gap = "6px";

      function tabLabel(id: number) {
        return `chat-${id}`;
      }

      function getActiveSessionID() {
        return `zotero:chat-${activeTabId}`;
      }

      function getSessionIDByTab(tabId: number) {
        return `zotero:chat-${tabId}`;
      }

      function nextAvailableTabID() {
        for (let id = 1; id <= MAX_CHAT_TABS; id++) {
          if (!chatTabs.some((t) => t.id === id)) {
            return id;
          }
        }
        return null;
      }

      function getTabHistory(tabId: number) {
        const records = chatHistoryByTab.get(tabId);
        if (records) return records;
        const init: ChatRecord[] = [];
        chatHistoryByTab.set(tabId, init);
        return init;
      }

      function setActiveTab(id: number) {
        activeTabId = id;
        for (const t of chatTabs) {
          const active = t.id === id;
          t.chip.style.background = active ? "rgba(125, 125, 125, 0.22)" : "rgba(125, 125, 125, 0.18)";
          t.labelBtn.style.fontWeight = active ? "700" : "500";
          t.closeBtn.style.opacity = active ? "0.88" : "0.7";
        }
      }

      function closeTab(id: number) {
        if (chatTabs.length <= 1) {
          return;
        }
        const idx = chatTabs.findIndex((t) => t.id === id);
        if (idx < 0) {
          return;
        }
        const [removed] = chatTabs.splice(idx, 1);
        removed.chip.remove();
        if (activeTabId === id) {
          const fallback = chatTabs[Math.max(0, idx - 1)] ?? chatTabs[0];
          setActiveTab(fallback.id);
          renderActiveTabConversation();
        } else {
          setActiveTab(activeTabId);
        }
        syncAddTabButtonState();
      }

      function createTab(id: number) {
        const chip = ownerDoc.createElement("div");
        chip.style.display = "inline-flex";
        chip.style.alignItems = "center";
        chip.style.gap = "4px";
        // 更紧凑的标签高度，减少上下留白
        chip.style.padding = "1px 6px 1px 10px";
        chip.style.borderRadius = "999px";
        chip.style.background = "rgba(125, 125, 125, 0.18)";
        chip.style.border = "none";

        const modeChipLabel = ownerDoc.createElement("button");
        modeChipLabel.type = "button";
        modeChipLabel.textContent = tabLabel(id);
        modeChipLabel.style.padding = "0";
        modeChipLabel.style.margin = "0";
        modeChipLabel.style.border = "none";
        modeChipLabel.style.background = "transparent";
        modeChipLabel.style.fontSize = "12px";
        modeChipLabel.style.cursor = "pointer";
        modeChipLabel.style.lineHeight = "1.2";
        modeChipLabel.addEventListener("click", () => {
          setActiveTab(id);
          renderActiveTabConversation();
        });

        const closeChipBtn = ownerDoc.createElement("button");
        closeChipBtn.type = "button";
        closeChipBtn.textContent = "×";
        closeChipBtn.title = "关闭标签";
        closeChipBtn.style.padding = "0";
        closeChipBtn.style.margin = "0";
        closeChipBtn.style.width = "16px";
        closeChipBtn.style.height = "16px";
        closeChipBtn.style.display = "inline-flex";
        closeChipBtn.style.alignItems = "center";
        closeChipBtn.style.justifyContent = "center";
        closeChipBtn.style.border = "none";
        closeChipBtn.style.borderRadius = "999px";
        closeChipBtn.style.background = "transparent";
        closeChipBtn.style.fontSize = "12px";
        closeChipBtn.style.lineHeight = "1";
        closeChipBtn.style.cursor = "pointer";
        closeChipBtn.style.opacity = "0.7";
        closeChipBtn.style.transition = "background 120ms ease, opacity 120ms ease";
        closeChipBtn.addEventListener("mouseenter", () => {
          if (closeChipBtn.disabled) return;
          closeChipBtn.style.background = "rgba(127, 127, 127, 0.24)";
          closeChipBtn.style.opacity = "1";
        });
        closeChipBtn.addEventListener("mouseleave", () => {
          if (closeChipBtn.disabled) return;
          closeChipBtn.style.background = "transparent";
          const active = activeTabId === id;
          closeChipBtn.style.opacity = active ? "0.88" : "0.7";
        });
        closeChipBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          closeTab(id);
        });

        chip.append(modeChipLabel, closeChipBtn);
        tabBar.appendChild(chip);
        chatTabs.push({ id, chip, labelBtn: modeChipLabel, closeBtn: closeChipBtn });
      }

      // 默认只展示 chat-1；其余标签在恢复历史时“有记录才显示”。
      createTab(1);
      setActiveTab(1);

      const addTabBtn = ownerDoc.createElement("button");
      addTabBtn.type = "button";
      addTabBtn.textContent = "+";
      addTabBtn.style.padding = "2px 8px";
      addTabBtn.style.borderRadius = "999px";
      addTabBtn.style.border = "none";
      addTabBtn.style.background = "rgba(125, 125, 125, 0.18)";
      addTabBtn.style.fontSize = "14px";
      addTabBtn.style.cursor = "pointer";
      addTabBtn.title = `最多可打开 ${MAX_CHAT_TABS} 个标签`;
      function syncAddTabButtonState() {
        const canAdd = chatTabs.length < MAX_CHAT_TABS;
        addTabBtn.disabled = !canAdd;
        addTabBtn.style.opacity = canAdd ? "1" : "0.45";
        addTabBtn.style.cursor = canAdd ? "pointer" : "not-allowed";
        const canClose = chatTabs.length > 1;
        for (const t of chatTabs) {
          t.closeBtn.disabled = !canClose;
          if (!canClose) {
            t.closeBtn.style.opacity = "0.35";
            t.closeBtn.style.cursor = "not-allowed";
          } else {
            t.closeBtn.style.cursor = "pointer";
            t.closeBtn.style.opacity = t.id === activeTabId ? "0.88" : "0.7";
          }
        }
      }
      addTabBtn.addEventListener("click", () => {
        if (chatTabs.length >= MAX_CHAT_TABS) {
          return;
        }
        const id = nextAvailableTabID();
        if (id == null) return;
        createTab(id);
        setActiveTab(id);
        renderActiveTabConversation();
        syncAddTabButtonState();
      });
      syncAddTabButtonState();

      const tabRow = ownerDoc.createElement("div");
      tabRow.style.display = "flex";
      tabRow.style.alignItems = "center";
      tabRow.style.gap = "6px";
      tabRow.style.flex = "1";
      tabRow.style.minWidth = "0";

      const clearChatBtn = ownerDoc.createElement("button");
      clearChatBtn.type = "button";
      clearChatBtn.title = "清除当前对话";
      clearChatBtn.style.display = "inline-flex";
      clearChatBtn.style.alignItems = "center";
      clearChatBtn.style.justifyContent = "center";
      clearChatBtn.style.padding = "2px";
      clearChatBtn.style.width = "28px";
      clearChatBtn.style.height = "28px";
      clearChatBtn.style.borderRadius = "999px";
      clearChatBtn.style.border = "none";
      clearChatBtn.style.background = "rgba(125, 125, 125, 0.18)";
      clearChatBtn.style.cursor = "pointer";
      clearChatBtn.style.flexShrink = "0";
      clearChatBtn.style.boxSizing = "border-box";
      const clearIcon = ownerDoc.createElement("img");
      clearIcon.src = `chrome://${config.addonRef}/content/icons/clear.svg`;
      clearIcon.alt = "";
      clearIcon.style.width = "16px";
      clearIcon.style.height = "16px";
      clearIcon.style.display = "block";
      clearIcon.style.opacity = "0.88";
      clearChatBtn.appendChild(clearIcon);

      tabRow.append(tabBar, addTabBtn);
      topBar.append(tabRow, clearChatBtn);

      // 对话区域
      const conversationArea = ownerDoc.createElement("div");
      conversationArea.style.flex = "1";
      conversationArea.style.minHeight = "180px";
      // 允许子项在 flex 布局下收缩，避免内层 <pre> 长行把整个面板撑出横向滚动
      conversationArea.style.minWidth = "0";
      conversationArea.style.overflowX = "hidden";
      conversationArea.style.border = "1px solid var(--color-border)";
      conversationArea.style.borderRadius = "10px";
      conversationArea.style.padding = "10px";
      // 保持滚动条槽位恒定，避免内容高度变化时“突然出现”导致视觉跳动
      conversationArea.style.overflowY = "scroll";
      conversationArea.style.userSelect = "text";
      (conversationArea.style as any).MozUserSelect = "text";
      conversationArea.style.fontSize = "13px";
      conversationArea.style.display = "flex";
      conversationArea.style.flexDirection = "column";
      conversationArea.style.gap = "8px";
      const SCROLL_BOTTOM_THRESHOLD_PX = 32;
      let autoStickToBottom = true;
      const isNearBottom = () => {
        const distance =
          conversationArea.scrollHeight - (conversationArea.scrollTop + conversationArea.clientHeight);
        return distance <= SCROLL_BOTTOM_THRESHOLD_PX;
      };
      const scrollConversationToBottom = (force = false) => {
        if (force || autoStickToBottom || isNearBottom()) {
          conversationArea.scrollTop = conversationArea.scrollHeight;
        }
      };
      conversationArea.addEventListener("scroll", () => {
        autoStickToBottom = isNearBottom();
      });
      let emptyStateEl: HTMLDivElement | null = null;

      function showEmptyState() {
        if (emptyStateEl) return;
        const box = ownerDoc.createElement("div");
        box.style.display = "flex";
        box.style.flexDirection = "column";
        box.style.alignItems = "center";
        box.style.justifyContent = "center";
        box.style.minHeight = "220px";
        box.style.padding = "20px 10px";
        box.style.textAlign = "center";

        const title = ownerDoc.createElement("div");
        title.textContent = "FireFly-Agent-Zotero";
        title.style.marginTop = "26px";
        title.style.fontSize = "24px";
        title.style.fontWeight = "700";
        title.style.lineHeight = "1.18";
        title.style.letterSpacing = "0.01em";
        title.style.maxWidth = "fit-content";
        title.style.marginLeft = "auto";
        title.style.marginRight = "auto";
        title.style.whiteSpace = "nowrap";

        const sub = ownerDoc.createElement("div");
        sub.textContent = "和流萤一起，探索整个Paper";
        sub.style.marginTop = "10px";
        sub.style.fontSize = "18px";
        sub.style.opacity = "0.52";
        sub.style.fontStyle = "italic";
        sub.style.lineHeight = "1.36";
        sub.style.maxWidth = "fit-content";
        sub.style.marginLeft = "auto";
        sub.style.marginRight = "auto";
        sub.style.whiteSpace = "nowrap";

        const greeting = ownerDoc.createElement("div");
        greeting.textContent =
          "我曾安眠，赤染之萤自破碎的天空坠落，\n沉睡在静默的星河。\n我梦见一片焦土，一株破土而出的新蕊，\n它迎着朝阳绽放，向我低语呢喃。\n飞萤扑火，向死而生。\n我会看见，飞萤之火自无梦的长夜亮起，\n绽放在终竟的明天。";
        greeting.style.marginTop = "100px";
        greeting.style.width = "min(92%, 720px)";
        greeting.style.marginLeft = "auto";
        greeting.style.marginRight = "auto";
        greeting.style.fontSize = "15px";
        greeting.style.lineHeight = "1.7";
        greeting.style.opacity = "0.62";
        greeting.style.fontStyle = "normal";
        greeting.style.fontWeight = "400";
        greeting.style.whiteSpace = "pre-wrap";

        box.append(title, sub, greeting);
        emptyStateEl = box;
        conversationArea.appendChild(box);
      }

      function hideEmptyState() {
        if (!emptyStateEl) return;
        emptyStateEl.remove();
        emptyStateEl = null;
      }

      showEmptyState();

      const composeCard = ownerDoc.createElement("div");
      composeCard.style.border = "1px solid var(--color-border)";
      composeCard.style.borderRadius = "12px";
      composeCard.style.padding = "6px 10px 10px";
      composeCard.style.display = "flex";
      composeCard.style.flexDirection = "column";
      composeCard.style.gap = "6px";
      composeCard.style.background = "#ffffff";

      const composeMeta = ownerDoc.createElement("div");
      composeMeta.style.display = "flex";
      composeMeta.style.gap = "8px";
      composeMeta.style.justifyContent = "flex-end";
      composeMeta.style.alignItems = "center";
      composeMeta.style.minHeight = "30px";
      composeMeta.style.height = "30px";
      composeMeta.style.flexWrap = "nowrap";
      composeMeta.style.overflow = "hidden";
      composeMeta.style.fontSize = "12px";
      composeMeta.style.opacity = "0.85";
      const bridgeStatus = ownerDoc.createElement("span");
      bridgeStatus.style.display = "inline-flex";
      bridgeStatus.style.alignItems = "center";
      bridgeStatus.style.gap = "5px";
      bridgeStatus.style.padding = "0px 6px";
      bridgeStatus.style.minHeight = "24px";
      bridgeStatus.style.borderRadius = "999px";
      bridgeStatus.style.border = "1px solid rgba(120, 120, 120, 0.24)";
      bridgeStatus.style.background = "rgba(127, 127, 127, 0.08)";
      bridgeStatus.style.whiteSpace = "nowrap";
      bridgeStatus.style.flexShrink = "0";
      const bridgeDot = ownerDoc.createElement("span");
      bridgeDot.style.width = "8px";
      bridgeDot.style.height = "8px";
      bridgeDot.style.borderRadius = "999px";
      bridgeDot.style.background = "rgba(130, 130, 130, 0.9)";
      bridgeDot.style.flexShrink = "0";
      const bridgeStatusText = ownerDoc.createElement("span");
      bridgeStatusText.textContent = "FireFly checking...";
      bridgeStatusText.style.fontSize = "12px";
      bridgeStatusText.style.fontWeight = "600";
      bridgeStatusText.style.opacity = "0.9";
      bridgeStatusText.style.lineHeight = "1";
      bridgeStatus.append(bridgeDot, bridgeStatusText);
      const contextBar = ownerDoc.createElement("div");
      contextBar.style.display = "flex";
      contextBar.style.flexWrap = "nowrap";
      contextBar.style.gap = "8px";
      contextBar.style.alignItems = "center";
      contextBar.style.flex = "1";
      contextBar.style.minHeight = "30px";
      contextBar.style.height = "30px";
      contextBar.style.overflowX = "auto";
      contextBar.style.overflowY = "hidden";
      contextBar.style.padding = "0 2px";
      contextBar.style.marginRight = "auto";
      composeMeta.append(contextBar, bridgeStatus);

      const TEXT_CONTEXT_PREFIX = "[Text Context]\n";
      const TEXT_CONTEXT_SUFFIX = "\n[/Text Context]\n\n";
      type ImageContextRecord = { path: string; name: string; mime: string; previewUrl: string };
      let textContextValues: string[] = [];
      let imageContextValues: ImageContextRecord[] = [];
      let hasReceivedLiterature = Boolean((globalThis as any).__fireflyWikiPdfReceived);
      let isConvertingLiterature = false;
      let currentModelLabel = "model";
      const refreshModelLabel = async () => {
        try {
          await ensureFireFlyBridgeStarted();
          const meta = await fetchBridgeMeta();
          const model = String(meta.model || "").trim();
          if (model) {
            currentModelLabel = model;
          }
        } catch {
          // ignore model label refresh failures
        }
      };
      const resolveCurrentPDFName = (): string => {
        const fallback = "当前 PDF";
        const selectedItems = ztoolkit.getGlobal("ZoteroPane")?.getSelectedItems?.() ?? [];
        const currentItem = selectedItems[0] ?? item;
        if (!currentItem) return fallback;
        const fromFilename = String((currentItem as any).getFilename?.() || "").trim();
        if (fromFilename) return fromFilename;
        const fromTitle = String((currentItem as any).getField?.("title") || "").trim();
        if (fromTitle) return fromTitle;
        const fromDisplayTitle = String((currentItem as any).getDisplayTitle?.() || "").trim();
        if (fromDisplayTitle) return fromDisplayTitle;
        return fallback;
      };
      /** 当前选中条目的文献题名（优先 Zotero「标题」字段） */
      const resolveCurrentLiteratureTitle = (): string => {
        const selectedItems = ztoolkit.getGlobal("ZoteroPane")?.getSelectedItems?.() ?? [];
        const currentItem = selectedItems[0] ?? item;
        if (!currentItem) return "";
        const title = String((currentItem as any).getField?.("title") || "").trim();
        if (title) return title;
        const display = String((currentItem as any).getDisplayTitle?.() || "").trim();
        if (display) return display;
        return String((currentItem as any).getFilename?.() || "").trim();
      };
      const currentPDFName = resolveCurrentPDFName();
      const inferImageExtension = (mime: string) => {
        const normalized = String(mime || "").toLowerCase();
        if (normalized.includes("png")) return "png";
        if (normalized.includes("jpeg") || normalized.includes("jpg")) return "jpg";
        if (normalized.includes("webp")) return "webp";
        if (normalized.includes("gif")) return "gif";
        return "png";
      };
      const saveImageBytesToTemp = async (
        bytes: Uint8Array,
        mime: string,
        source: "snip" | "paste",
      ): Promise<{ path: string; name: string; mime: string }> => {
        const g = globalThis as any;
        const pathUtils = g.PathUtils;
        const ioUtils = g.IOUtils;
        if (!pathUtils?.join || !pathUtils?.tempDir || !ioUtils?.write) {
          throw new Error("当前环境不支持图片暂存");
        }
        const ext = inferImageExtension(mime);
        const stamp = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
        const filename = `firefly-${source}-${stamp}.${ext}`;
        const absPath = pathUtils.join(pathUtils.tempDir, filename);
        await ioUtils.write(absPath, bytes);
        return { path: String(absPath), name: filename, mime };
      };
      const addImageContextFromBlob = async (blob: Blob, source: "snip" | "paste") => {
        const mime = blob.type || "image/png";
        const buf = new Uint8Array(await blob.arrayBuffer());
        const saved = await saveImageBytesToTemp(buf, mime, source);
        const previewUrl =
          (ownerDoc.defaultView?.URL || URL).createObjectURL(
            new Blob([buf], { type: mime || "image/png" }),
          );
        imageContextValues.push({ ...saved, previewUrl });
        syncContextBar();
      };
      let imagePreviewTooltipEl: HTMLDivElement | null = null;
      let imagePreviewTooltipImg: HTMLImageElement | null = null;
      let imagePreviewTooltipCaption: HTMLDivElement | null = null;
      const ensureImagePreviewTooltip = () => {
        if (imagePreviewTooltipEl && imagePreviewTooltipImg && imagePreviewTooltipCaption) {
          return {
            root: imagePreviewTooltipEl,
            img: imagePreviewTooltipImg,
            caption: imagePreviewTooltipCaption,
          };
        }
        const root = ownerDoc.createElement("div");
        root.style.position = "fixed";
        root.style.zIndex = "2147483647";
        root.style.display = "none";
        root.style.pointerEvents = "none";
        root.style.border = "1px solid rgba(120, 120, 120, 0.4)";
        root.style.background = "rgba(255, 255, 255, 0.98)";
        root.style.borderRadius = "10px";
        root.style.padding = "8px";
        root.style.boxShadow = "0 8px 22px rgba(0, 0, 0, 0.2)";
        root.style.maxWidth = "340px";
        const img = ownerDoc.createElement("img");
        img.style.display = "block";
        img.style.maxWidth = "320px";
        img.style.maxHeight = "220px";
        img.style.objectFit = "contain";
        img.style.borderRadius = "6px";
        const caption = ownerDoc.createElement("div");
        caption.style.display = "none";
        root.append(img, caption);
        (ownerDoc.body || ownerDoc.documentElement)?.appendChild(root);
        imagePreviewTooltipEl = root;
        imagePreviewTooltipImg = img;
        imagePreviewTooltipCaption = caption;
        return { root, img, caption };
      };
      const hideImagePreviewTooltip = () => {
        if (!imagePreviewTooltipEl) return;
        imagePreviewTooltipEl.style.display = "none";
      };
      const showImagePreviewTooltip = (anchorEl: HTMLElement, imgRec: ImageContextRecord) => {
        const tooltip = ensureImagePreviewTooltip();
        tooltip.img.src = imgRec.previewUrl;
        tooltip.caption.textContent = "";
        tooltip.root.style.display = "block";
        const w = ownerDoc.defaultView?.innerWidth ?? 1200;
        const h = ownerDoc.defaultView?.innerHeight ?? 800;
        const rect = anchorEl.getBoundingClientRect();
        // 先显示再测量，保证使用真实尺寸做“标签上方居中”定位。
        const tooltipWidth = Math.max(1, tooltip.root.offsetWidth || 340);
        const tooltipHeight = Math.max(1, tooltip.root.offsetHeight || 280);
        const centerX = rect.left + rect.width / 2;
        const left = Math.min(Math.max(8, centerX - tooltipWidth / 2), w - tooltipWidth - 8);
        // 固定贴在标签上方；若顶部空间不足再回退到标签下方。
        const preferredTop = rect.top - tooltipHeight - 8;
        const fallbackTop = rect.bottom + 8;
        const top = preferredTop >= 8 ? preferredTop : Math.min(fallbackTop, h - tooltipHeight - 8);
        tooltip.root.style.left = `${Math.max(8, left)}px`;
        tooltip.root.style.top = `${Math.max(8, top)}px`;
      };

      const buildContextPayload = () =>
        textContextValues
          .map((v) => `${TEXT_CONTEXT_PREFIX}${v}${TEXT_CONTEXT_SUFFIX}`)
          .join("");

      const syncContextBar = () => {
        contextBar.replaceChildren();
        const has = textContextValues.length > 0 || imageContextValues.length > 0;
        contextBar.style.display = "flex";
        contextBar.style.justifyContent = has ? "flex-start" : "center";
        if (!has) {
          const pdfLabel = ownerDoc.createElement("span");
          const idleText = isConvertingLiterature
            ? "流萤正在阅读你的文献"
            : hasReceivedLiterature
              ? "流萤已经阅读了你的文献"
              : "流萤在等待你的提问";
          const idleIcon = ownerDoc.createElement("img");
          idleIcon.src = `chrome://${config.addonRef}/content/icons/firefly_smell.svg`;
          idleIcon.alt = "firefly";
          idleIcon.style.width = "28px";
          idleIcon.style.height = "28px";
          idleIcon.style.opacity = "1";
          idleIcon.style.flexShrink = "0";

          const idleTextEl = ownerDoc.createElement("span");
          idleTextEl.textContent = idleText;
          idleTextEl.style.opacity = "0.82";
          idleTextEl.style.color = "rgba(70, 70, 70, 0.95)";
          pdfLabel.title = idleText;
          pdfLabel.style.display = "inline-flex";
          pdfLabel.style.alignItems = "center";
          pdfLabel.style.justifyContent = "center";
          pdfLabel.style.gap = "8px";
          pdfLabel.style.flex = "1";
          pdfLabel.style.padding = "1px 0";
          pdfLabel.style.minHeight = "24px";
          pdfLabel.style.minWidth = "0";
          pdfLabel.style.maxWidth = "100%";
          pdfLabel.style.overflow = "hidden";
          pdfLabel.style.textOverflow = "ellipsis";
          pdfLabel.style.whiteSpace = "nowrap";
          pdfLabel.style.fontSize = "12px";
          pdfLabel.style.fontWeight = "600";
          pdfLabel.style.opacity = "1";
          pdfLabel.style.fontStyle = "italic";
          pdfLabel.style.lineHeight = "1.2";
          pdfLabel.append(idleIcon, idleTextEl);
          contextBar.appendChild(pdfLabel);
          return;
        }
        const buildChip = (
          label: string,
          title: string,
          iconSrc: string,
          onHoverBind: ((chip: HTMLDivElement, icon: HTMLImageElement, labelEl: HTMLSpanElement) => void) | null,
          onRemove: () => void,
        ) => {
          const chip = ownerDoc.createElement("div");
          chip.style.display = "inline-flex";
          chip.style.alignItems = "center";
          chip.style.gap = "5px";
          chip.style.padding = "0px 7px";
          chip.style.minHeight = "24px";
          chip.style.borderRadius = "999px";
          chip.style.background = "rgba(127, 127, 127, 0.08)";
          chip.style.border = "1px solid rgba(120, 120, 120, 0.24)";
          chip.style.whiteSpace = "nowrap";
          chip.title = title;

          const chipIcon = ownerDoc.createElement("img");
          chipIcon.src = iconSrc;
          chipIcon.alt = "context";
          chipIcon.style.width = "13px";
          chipIcon.style.height = "13px";
          chipIcon.style.opacity = "0.75";

          const chipLabel = ownerDoc.createElement("span");
          chipLabel.textContent = label;
          chipLabel.style.fontSize = "12px";
          chipLabel.style.fontWeight = "600";
          chipLabel.style.opacity = "0.9";
          chipLabel.style.lineHeight = "1";

          const chipClose = ownerDoc.createElement("button");
          chipClose.type = "button";
          chipClose.textContent = "×";
          chipClose.style.border = "none";
          chipClose.style.background = "transparent";
          chipClose.style.cursor = "pointer";
          chipClose.style.fontSize = "14px";
          chipClose.style.lineHeight = "1";
          chipClose.style.opacity = "0.5";
          chipClose.style.padding = "0 1px";
          chipClose.addEventListener("mouseenter", () => (chipClose.style.opacity = "0.85"));
          chipClose.addEventListener("mouseleave", () => (chipClose.style.opacity = "0.55"));
          chipClose.addEventListener("click", onRemove);

          chip.append(chipIcon, chipLabel, chipClose);
          if (onHoverBind) {
            onHoverBind(chip, chipIcon, chipLabel);
          }
          contextBar.appendChild(chip);
        };
        textContextValues.forEach((_, idx) => {
          buildChip(
            `Text-${idx + 1}`,
            textContextValues[idx] || "",
            `chrome://${config.addonRef}/content/icons/copy.svg`,
            null,
            () => {
              textContextValues = textContextValues.filter((__, i) => i !== idx);
              syncContextBar();
              focusTextAreaNoScroll();
            },
          );
        });
        imageContextValues.forEach((img, idx) => {
          buildChip(
            `Image-${idx + 1}`,
            "",
            `chrome://${config.addonRef}/content/icons/picture.svg`,
            (chip, icon, labelEl) => {
              const bind = (el: HTMLElement) => {
                el.addEventListener("mouseenter", () => showImagePreviewTooltip(chip, img));
                el.addEventListener("mousemove", () => showImagePreviewTooltip(chip, img));
                el.addEventListener("mouseleave", () => hideImagePreviewTooltip());
              };
              bind(chip);
              bind(icon);
              bind(labelEl);
            },
            () => {
              try {
                (ownerDoc.defaultView?.URL || URL).revokeObjectURL(img.previewUrl);
              } catch {
                // ignore revoke failures
              }
              imageContextValues = imageContextValues.filter((__, i) => i !== idx);
              hideImagePreviewTooltip();
              syncContextBar();
              focusTextAreaNoScroll();
            },
          );
        });
      };
      syncContextBar();
      ownerDoc.defaultView?.addEventListener("firefly-wiki-pdf-received", () => {
        hasReceivedLiterature = true;
        syncContextBar();
      });
      void refreshModelLabel();

      const textArea = ownerDoc.createElement("textarea");
      textArea.placeholder = "流萤好奇你的疑问...";
      textArea.style.minHeight = "90px";
      textArea.style.resize = "vertical";
      textArea.style.userSelect = "text";
      (textArea.style as any).MozUserSelect = "text";
      const focusTextAreaNoScroll = () => {
        try {
          (textArea as any).focus({ preventScroll: true });
        } catch {
          textArea.focus();
        }
      };
      textArea.addEventListener("paste", (ev: ClipboardEvent) => {
        const items = Array.from(ev.clipboardData?.items || []);
        const imageItems = items.filter((item) => item.type?.startsWith("image/"));
        if (!imageItems.length) return;
        ev.preventDefault();
        for (const item of imageItems) {
          const file = item.getAsFile();
          if (!file) continue;
          void (async () => {
            try {
              await addImageContextFromBlob(file, "paste");
              focusTextAreaNoScroll();
            } catch (e) {
              ztoolkit.log("[llm-ui] paste image failed:", String(e));
              appendBubble("system", `粘贴图片失败: ${String(e)}`);
            }
          })();
        }
      });

      const composeActions = ownerDoc.createElement("div");
      composeActions.style.display = "flex";
      composeActions.style.justifyContent = "space-between";
      composeActions.style.alignItems = "center";
      composeActions.style.gap = "12px";

      const leftActions = ownerDoc.createElement("div");
      leftActions.style.display = "inline-flex";
      leftActions.style.alignItems = "center";
      leftActions.style.gap = "8px";
      leftActions.style.minWidth = "0";

      const iconBase = `chrome://${config.addonRef}/content/icons`;
      const createToolBtn = (title: string) => {
        const btn = ownerDoc.createElement("button");
        btn.type = "button";
        btn.title = title;
        btn.style.width = "28px";
        btn.style.height = "28px";
        btn.style.border = "none";
        btn.style.borderRadius = "8px";
        btn.style.background = "transparent";
        btn.style.display = "inline-flex";
        btn.style.alignItems = "center";
        btn.style.justifyContent = "center";
        btn.style.cursor = "pointer";
        btn.style.transition = "background 120ms ease";
        btn.addEventListener("mouseenter", () => {
          btn.style.background = "rgba(127, 127, 127, 0.16)";
        });
        btn.addEventListener("mouseleave", () => {
          btn.style.background = "transparent";
        });
        return btn;
      };
      /** 防止 Zotero Item Pane 上层抢占 mousedown/click，确保工具栏按钮可触发。 */
      const bindPaneActionButton = (btn: HTMLButtonElement, handler: () => void) => {
        btn.addEventListener("mousedown", (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
        });
        btn.addEventListener(
          "click",
          (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            handler();
          },
          { capture: true },
        );
      };
      const appendToolIcon = (btn: HTMLButtonElement, iconSrc: string, alt: string, sizePx = 16) => {
        const icon = ownerDoc.createElement("img");
        icon.src = iconSrc;
        icon.alt = alt;
        icon.style.width = `${sizePx}px`;
        icon.style.height = `${sizePx}px`;
        icon.style.pointerEvents = "none";
        btn.appendChild(icon);
        return icon;
      };

      const slashBtn = createToolBtn("命令");
      appendToolIcon(slashBtn, `${iconBase}/bars.svg`, "command");

      function getSelectedTextFromZotero(): string {
        const mainWin = Zotero.getMainWindow() as Window | null;
        const focusedWin =
          ((mainWin as any)?.document?.commandDispatcher?.focusedWindow as Window | null) ?? null;
        const winCandidates = [focusedWin, ownerDoc.defaultView as Window | null, mainWin].filter(
          (v): v is Window => !!v,
        );
        for (const win of winCandidates) {
          try {
            const selected = win.getSelection?.()?.toString()?.trim() ?? "";
            if (selected) return selected;
          } catch {
            // ignore
          }
        }
        const activeEl =
          (focusedWin?.document?.activeElement as HTMLInputElement | HTMLTextAreaElement | null) ??
          (mainWin?.document?.activeElement as HTMLInputElement | HTMLTextAreaElement | null) ??
          null;
        if (
          activeEl &&
          typeof activeEl.value === "string" &&
          typeof activeEl.selectionStart === "number" &&
          typeof activeEl.selectionEnd === "number" &&
          activeEl.selectionEnd > activeEl.selectionStart
        ) {
          return activeEl.value.slice(activeEl.selectionStart, activeEl.selectionEnd).trim();
        }
        return "";
      }

      const fontBtn = createToolBtn("截取文字");
      appendToolIcon(fontBtn, `${iconBase}/font-size.svg`, "font");
      const applySelectedTextAsContext = () => {
        const selectedText = getSelectedTextFromZotero();
        if (!selectedText) {
          ztoolkit.log("[llm-ui] 未检测到可插入的划选文字");
          return;
        }
        // 仅在后台携带上下文，不污染用户输入框。
        textContextValues.push(selectedText);
        syncContextBar();
        focusTextAreaNoScroll();
      };
      // 在 mousedown 阶段读取选区，避免点击按钮导致 Zotero 清空 selection。
      fontBtn.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        applySelectedTextAsContext();
      });
      // 兜底：若某些环境下 mousedown 未触发/无效，再在 click 时尝试一次。
      fontBtn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        applySelectedTextAsContext();
      });

      const screenshotBtn = createToolBtn("截图工具");
      appendToolIcon(screenshotBtn, `${iconBase}/picture.svg`, "image");
      const startAreaScreenshotCapture = async () => {
        const captureWin = ownerDoc.defaultView as Window | null;
        if (!captureWin) {
          throw new Error("无法访问当前窗口");
        }
        const selection = await new Promise<{ left: number; top: number; width: number; height: number } | null>(
          (resolve) => {
            const overlay = ownerDoc.createElement("div");
            overlay.style.position = "fixed";
            overlay.style.left = "0";
            overlay.style.top = "0";
            overlay.style.right = "0";
            overlay.style.bottom = "0";
            overlay.style.zIndex = "2147483646";
            overlay.style.background = "rgba(0, 0, 0, 0.08)";
            overlay.style.cursor = "crosshair";
            overlay.style.userSelect = "none";
            const box = ownerDoc.createElement("div");
            box.style.position = "fixed";
            box.style.border = "1px solid rgba(47, 110, 232, 0.95)";
            box.style.background = "rgba(47, 110, 232, 0.14)";
            box.style.display = "none";
            box.style.pointerEvents = "none";
            box.style.zIndex = "2147483647";
            overlay.appendChild(box);
            const host = ownerDoc.body || ownerDoc.documentElement;
            if (!host) {
              resolve(null);
              return;
            }
            host.appendChild(overlay);
            let dragging = false;
            let startX = 0;
            let startY = 0;
            const cleanup = (rect: { left: number; top: number; width: number; height: number } | null) => {
              overlay.removeEventListener("mousedown", onMouseDown);
              overlay.removeEventListener("mousemove", onMouseMove);
              overlay.removeEventListener("mouseup", onMouseUp);
              captureWin.removeEventListener("keydown", onKeyDown, true);
              overlay.remove();
              resolve(rect);
            };
            const onMouseDown = (ev: MouseEvent) => {
              ev.preventDefault();
              dragging = true;
              startX = ev.clientX;
              startY = ev.clientY;
              box.style.display = "block";
              box.style.left = `${startX}px`;
              box.style.top = `${startY}px`;
              box.style.width = "0px";
              box.style.height = "0px";
            };
            const onMouseMove = (ev: MouseEvent) => {
              if (!dragging) return;
              const left = Math.min(startX, ev.clientX);
              const top = Math.min(startY, ev.clientY);
              const width = Math.abs(ev.clientX - startX);
              const height = Math.abs(ev.clientY - startY);
              box.style.left = `${left}px`;
              box.style.top = `${top}px`;
              box.style.width = `${width}px`;
              box.style.height = `${height}px`;
            };
            const onMouseUp = (ev: MouseEvent) => {
              if (!dragging) {
                cleanup(null);
                return;
              }
              dragging = false;
              const left = Math.min(startX, ev.clientX);
              const top = Math.min(startY, ev.clientY);
              const width = Math.abs(ev.clientX - startX);
              const height = Math.abs(ev.clientY - startY);
              if (width < 4 || height < 4) {
                cleanup(null);
                return;
              }
              cleanup({ left, top, width, height });
            };
            const onKeyDown = (ev: KeyboardEvent) => {
              if (ev.key === "Escape") {
                ev.preventDefault();
                cleanup(null);
              }
            };
            overlay.addEventListener("mousedown", onMouseDown);
            overlay.addEventListener("mousemove", onMouseMove);
            overlay.addEventListener("mouseup", onMouseUp);
            captureWin.addEventListener("keydown", onKeyDown, true);
          },
        );
        if (!selection) {
          return;
        }
        const canvas = ownerDoc.createElement("canvas");
        const dpr = Math.max(1, captureWin.devicePixelRatio || 1);
        canvas.width = Math.max(1, Math.round(selection.width * dpr));
        canvas.height = Math.max(1, Math.round(selection.height * dpr));
        const ctx: any = canvas.getContext("2d");
        if (!ctx || typeof ctx.drawWindow !== "function") {
          throw new Error("当前环境不支持区域截图");
        }
        ctx.save();
        ctx.scale(dpr, dpr);
        ctx.drawWindow(
          captureWin,
          selection.left,
          selection.top,
          selection.width,
          selection.height,
          "rgb(255,255,255)",
        );
        ctx.restore();
        const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
        if (!blob) {
          throw new Error("截图生成失败");
        }
        await addImageContextFromBlob(blob, "snip");
      };
      bindPaneActionButton(screenshotBtn, () => {
        if (isSending) return;
        void (async () => {
          try {
            await startAreaScreenshotCapture();
            focusTextAreaNoScroll();
          } catch (e) {
            ztoolkit.log("[llm-ui] screenshot failed:", String(e));
            appendBubble("system", `截图失败: ${String(e)}`);
          }
        })();
      });

      const convertBtn = createToolBtn("Markdown → RAG（一键）");
      appendToolIcon(convertBtn, `${iconBase}/convert.svg`, "convert");
      const syncConvertButtonState = () => {
        const disabled = isConvertingLiterature;
        convertBtn.disabled = disabled;
        convertBtn.style.opacity = disabled ? "0.5" : "1";
        convertBtn.style.cursor = disabled ? "not-allowed" : "pointer";
        convertBtn.title = disabled
          ? "流萤正在处理文献…"
          : "将当前文献切片写入 RAG：尚无 Markdown 时会从 PDF 生成；已有 Markdown 时只重建 RAG（不重转 PDF）";
      };
      const triggerCurrentPdfMarkdownConversion = async () => {
        if (isConvertingLiterature) return;
        isConvertingLiterature = true;
        syncConvertButtonState();
        syncContextBar();
        try {
          const resolved = await resolveWikiPdfInfo({ preferredItem: item });
          if (!resolved.ok) {
            appendBubble("system", resolved.message);
            return;
          }
          const pdfInfo = resolved.data;
          ztoolkit.log(
            "[llm-ui] convert-markdown start:",
            pdfInfo.wikiPdfPath,
            "←",
            pdfInfo.pdfPath,
          );
          appendBubble("system", "正在连接 FireFly Bridge…");
          await ensureFireFlyBridgeStarted();
          appendBubble("system", "正在同步 PDF 到 llm-wiki/raw/pdf…");
          let wikiMirrorJustCopied = false;
          try {
            wikiMirrorJustCopied = await ensureWikiPdfMirrorIfMissing(pdfInfo);
          } catch (syncErr) {
            appendBubble(
              "system",
              `无法同步 PDF 到 llm-wiki/raw/pdf：${String((syncErr as any)?.message || syncErr || "")}`,
            );
            return;
          }
          appendBubble("system", "正在从 PDF 生成 Markdown 并重建 RAG…");
          const result = await convertCurrentPdfToMarkdown({
            pdf_path: pdfInfo.pdfPath,
            pdf_dir: pdfInfo.pdfDir,
            pdf_name: pdfInfo.pdfName,
            wiki_pdf_path: pdfInfo.wikiPdfPath,
            rebuild_rag: true,
          });
          if (result && (result as any).ok === false) {
            throw new Error(String((result as any).error || "convert-markdown 返回失败"));
          }
          ztoolkit.log("[llm-ui] convert-markdown done:", JSON.stringify(result));
          hasReceivedLiterature = true;
          (globalThis as any).__fireflyWikiPdfReceived = true;
          // Zotero 部分文档的 defaultView 无全局 CustomEvent，勿直接使用裸的 CustomEvent 标识符。
          const win = ownerDoc.defaultView;
          if (win) {
            try {
              const CE = (win as any).CustomEvent;
              if (typeof CE === "function") {
                win.dispatchEvent(new CE("firefly-wiki-pdf-received"));
              } else {
                const ev = ownerDoc.createEvent("CustomEvent") as any;
                if (typeof ev?.initCustomEvent === "function") {
                  ev.initCustomEvent("firefly-wiki-pdf-received", false, false, null);
                  win.dispatchEvent(ev);
                }
              }
            } catch (dispatchErr) {
              ztoolkit.log("[llm-ui] firefly-wiki-pdf-received dispatch:", String(dispatchErr));
            }
          }
          syncContextBar();
          const markdownPath = String((result as any)?.markdown_path || "").trim();
          const mdReason = String((result as any)?.reason || "").trim();
          const mdConverted = Boolean((result as any)?.converted);
          const convertMsg =
            mdReason === "already_converted"
              ? `已有 Markdown，未从 PDF 重转${markdownPath ? `：${markdownPath}` : ""}`
              : mdConverted
                ? `已从 PDF 生成 Markdown${markdownPath ? `：${markdownPath}` : ""}`
                : `Markdown 已就绪${markdownPath ? `：${markdownPath}` : ""}`;
          const ragPath = String((result as any)?.rag_path || "").trim();
          const ragChunks = Number((result as any)?.rag_chunk_count ?? 0);
          const ragReindexed = Boolean((result as any)?.rag_reindexed);
          const ragLine =
            ragReindexed && Number.isFinite(ragChunks) && ragChunks > 0
              ? `RAG 已重新切片：${ragChunks} 段${ragPath ? ` → ${ragPath}` : ""}`
              : ragReindexed && ragPath
                ? `RAG 已重新切片 → ${ragPath}`
                : Number.isFinite(ragChunks) && ragChunks > 0
                  ? `RAG 索引已更新：${ragChunks} 段${ragPath ? ` → ${ragPath}` : ""}`
                  : ragPath
                    ? `RAG 索引已更新 → ${ragPath}`
                    : ragReindexed
                      ? "RAG 已重新切片"
                      : "RAG 索引已更新";
          const mirrorNote = wikiMirrorJustCopied ? "已从 Zotero 补全 wiki 目录中的 PDF 副本。\n" : "";
          appendBubble("system", `${mirrorNote}${convertMsg}\n${ragLine}`);
        } catch (e) {
          const raw = String((e as any)?.message || e || "");
          let detail = raw;
          const jsonStart = raw.indexOf("{");
          if (jsonStart >= 0) {
            try {
              const parsed = JSON.parse(raw.slice(jsonStart)) as { error?: string; error_log?: string };
              if (parsed.error) {
                detail = parsed.error;
              }
              if (parsed.error_log) {
                detail += `\n详细日志: ${parsed.error_log}`;
              }
            } catch {
              // keep raw message
            }
          }
          appendBubble("system", `Markdown 转换失败: ${detail}`);
          ztoolkit.log("[llm-ui] convert-markdown error:", raw);
        } finally {
          isConvertingLiterature = false;
          syncConvertButtonState();
          syncContextBar();
        }
      };
      bindPaneActionButton(convertBtn, () => {
        if (isSending) return;
        void triggerCurrentPdfMarkdownConversion();
      });
      syncConvertButtonState();

      const thinkingStateWrap = ownerDoc.createElement("button");
      thinkingStateWrap.type = "button";
      thinkingStateWrap.style.display = "inline-flex";
      thinkingStateWrap.style.alignItems = "center";
      thinkingStateWrap.style.gap = "4px";
      thinkingStateWrap.style.padding = "0 6px";
      thinkingStateWrap.style.height = "28px";
      thinkingStateWrap.style.border = "none";
      thinkingStateWrap.style.borderRadius = "8px";
      thinkingStateWrap.style.background = "transparent";
      thinkingStateWrap.style.cursor = "pointer";
      thinkingStateWrap.style.transition = "background 120ms ease";
      thinkingStateWrap.style.opacity = "1";
      thinkingStateWrap.addEventListener("mouseenter", () => {
        thinkingStateWrap.style.background = "rgba(127, 127, 127, 0.16)";
      });
      thinkingStateWrap.addEventListener("mouseleave", () => {
        thinkingStateWrap.style.background = "transparent";
      });
      appendToolIcon(thinkingStateWrap, `${iconBase}/think.svg`, "wiki", 18);
      leftActions.append(slashBtn, fontBtn, screenshotBtn, convertBtn, thinkingStateWrap);

      const sendBtn = ownerDoc.createElement("button");
      sendBtn.textContent = "Send";
      sendBtn.disabled = false;
      (sendBtn as HTMLButtonElement).type = "button";
      sendBtn.style.borderRadius = "999px";
      sendBtn.style.padding = "10px 22px";
      sendBtn.style.border = "none";
      const sendBgIdle = "linear-gradient(180deg, #3f83f8 0%, #2f6ee8 100%)";
      const sendBgIdleHover = "linear-gradient(180deg, #3274ed 0%, #265fdb 100%)";
      const sendBgCancel = "linear-gradient(180deg, #eb5757 0%, #d13434 100%)";
      const sendBgCancelHover = "linear-gradient(180deg, #df4a4a 0%, #c52e2e 100%)";
      sendBtn.style.background = sendBgIdle;
      sendBtn.style.color = "#ffffff";
      sendBtn.style.fontWeight = "700";
      sendBtn.style.fontSize = "14px";
      sendBtn.style.lineHeight = "1.2";
      sendBtn.style.display = "inline-flex";
      sendBtn.style.alignItems = "center";
      sendBtn.style.justifyContent = "center";
      sendBtn.style.cursor = "pointer";
      sendBtn.style.boxShadow = "0 2px 6px rgba(47, 110, 232, 0.28)";
      sendBtn.style.transition = "background 140ms ease, transform 140ms ease, opacity 140ms ease";
      const createAbortControllerCompat = (): AbortController => {
        const AC = (globalThis as any).AbortController;
        if (AC) {
          return new AC() as AbortController;
        }
        let aborted = false;
        const listeners: Array<() => void> = [];
        const signal = {
          get aborted() {
            return aborted;
          },
          addEventListener: (_type: string, listener: any, options?: any) => {
            if (_type !== "abort" || !listener) return;
            const cb =
              typeof listener === "function"
                ? listener
                : typeof listener.handleEvent === "function"
                  ? () => listener.handleEvent()
                  : null;
            if (!cb) return;
            if (aborted) {
              cb();
              return;
            }
            if (options?.once) {
              const onceCb = () => {
                cb();
                const idx = listeners.indexOf(onceCb);
                if (idx >= 0) listeners.splice(idx, 1);
              };
              listeners.push(onceCb);
              return;
            }
            listeners.push(cb);
          },
        } as any;
        return {
          signal,
          abort: () => {
            if (aborted) return;
            aborted = true;
            const cbs = [...listeners];
            listeners.length = 0;
            for (const cb of cbs) {
              try {
                cb();
              } catch {
                // ignore callback failures
              }
            }
          },
        } as AbortController;
      };
      let isSending = false;
      let wikiIngestBusy = false;
      let activeStreamAbortController: AbortController | null = null;
      let isCanceling = false;
      const cancelActiveGeneration = async () => {
        if (!isSending || isCanceling) return;
        isCanceling = true;
        try {
          activeStreamAbortController?.abort();
          await cancelFireFlyStream(getActiveSessionID());
        } catch (e) {
          ztoolkit.log("[llm-ui] cancel request failed:", String((e as any)?.message || e || ""));
        } finally {
          isCanceling = false;
        }
      };
      const syncSendButtonState = (sending: boolean) => {
        isSending = sending;
        sendBtn.textContent = sending ? "Cancel" : "Send";
        sendBtn.style.background = sending ? sendBgCancel : sendBgIdle;
        sendBtn.style.boxShadow = sending
          ? "0 2px 6px rgba(209, 52, 52, 0.28)"
          : "0 2px 6px rgba(47, 110, 232, 0.28)";
        sendBtn.style.opacity = "1";
        sendBtn.style.cursor = "pointer";
      };
      sendBtn.addEventListener("mouseenter", () => {
        if (sendBtn.disabled) return;
        sendBtn.style.background = isSending ? sendBgCancelHover : sendBgIdleHover;
      });
      sendBtn.addEventListener("mouseleave", () => {
        if (sendBtn.disabled) return;
        sendBtn.style.background = isSending ? sendBgCancel : sendBgIdle;
      });
      syncSendButtonState(false);

      thinkingStateWrap.title = "通过本论文的 Markdown 整理 wiki（需已生成 raw/markdown）";
      const triggerWikiIngestFromCurrentItem = async () => {
        if (wikiIngestBusy) return;
        wikiIngestBusy = true;
        thinkingStateWrap.style.opacity = "0.55";
        try {
          appendBubble("system", "正在根据 Markdown 整理 wiki…");
          const resolved = await resolveWikiPdfInfo({ preferredItem: item });
          if (!resolved.ok) {
            appendBubble("system", resolved.message);
            return;
          }
          const pdfInfo = resolved.data;
          await ensureFireFlyBridgeStarted();
          try {
            await ensureWikiPdfMirrorIfMissing(pdfInfo);
          } catch (syncErr) {
            appendBubble(
              "system",
              `无法同步 PDF 到 llm-wiki/raw/pdf：${String((syncErr as any)?.message || syncErr || "")}`,
            );
            return;
          }
          const res = await triggerWikiIngestFromMarkdown({
            pdf_path: pdfInfo.pdfPath,
            pdf_dir: pdfInfo.pdfDir,
            pdf_name: pdfInfo.pdfName,
            wiki_pdf_path: pdfInfo.wikiPdfPath,
          });
          if (res.ok && res.detail === "written") {
            const trunc = res.source_truncated ? "\n（原文过长已截断，仅以前部为据）" : "";
            appendBubble("system", `Wiki 已写入：${res.wiki_path || ""}${trunc}`);
          } else {
            const hint =
              res.detail === "markdown_missing"
                ? "尚无 Markdown，请先点击「Markdown→RAG」从 PDF 生成。"
                : res.detail === "no_pdf"
                  ? "未找到 PDF。"
                  : res.detail === "no_llm_wiki"
                    ? "未在 workspace 旁找到 llm-wiki。"
                    : res.detail === "markdown_outside_raw_tree"
                      ? "Markdown 路径不在 raw/markdown 下。"
                      : res.detail === "markdown_read_error"
                        ? "无法读取 Markdown 文件。"
                        : res.detail === "llm_error"
                          ? "LLM 调用失败，请查看 FireFly 日志。"
                          : res.detail === "empty_llm_output"
                            ? "模型返回为空。"
                            : res.detail || "未知错误";
            appendBubble("system", `Wiki 整理未成功：${hint}`);
          }
        } catch (e) {
          appendBubble("system", `Wiki 整理请求失败：${String((e as any)?.message || e || "")}`);
        } finally {
          wikiIngestBusy = false;
          thinkingStateWrap.style.opacity = "1";
        }
      };
      bindPaneActionButton(thinkingStateWrap, () => {
        if (isSending || isConvertingLiterature || wikiIngestBusy) return;
        void triggerWikiIngestFromCurrentItem();
      });

      clearChatBtn.addEventListener("mousedown", (ev) => {
        ev.stopPropagation();
      });
      clearChatBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        void (async () => {
          try {
            if (isSending) {
              await cancelActiveGeneration();
              syncSendButtonState(false);
            }
          } catch {
            syncSendButtonState(false);
          }
          chatHistoryByTab.set(activeTabId, []);
          renderActiveTabConversation();
          try {
            await ensureFireFlyBridgeStarted();
            await clearZoteroSession(getSessionIDByTab(activeTabId));
          } catch (e) {
            ztoolkit.log("[llm-ui] clear session failed:", String((e as any)?.message || e || ""));
          }
        })();
      });

      composeActions.append(leftActions, sendBtn);
      composeCard.append(composeMeta, textArea, composeActions);

      function appendBubble(role: "user" | "system", text: string) {
        hideEmptyState();
        const row = ownerDoc.createElement("div");
        row.style.maxWidth = "90%";
        row.style.minWidth = "0";
        row.style.width = role === "user" ? "auto" : "100%";
        row.style.alignSelf = role === "user" ? "flex-end" : "flex-start";
        row.style.padding = "8px 10px";
        row.style.borderRadius = "10px";
        row.style.whiteSpace = "pre-wrap";
        row.style.wordBreak = "break-word";
        (row.style as any).overflowWrap = "anywhere";
        row.style.background =
          role === "user" ? "rgba(80, 140, 255, 0.18)" : "rgba(127, 127, 127, 0.12)";
        row.style.userSelect = "text";
        (row.style as any).MozUserSelect = "text";
        row.style.cursor = "text";
        row.textContent = text;
        conversationArea.appendChild(row);
        scrollConversationToBottom();
        return row;
      }

      function nowHM() {
        const d = new Date();
        const hh = String(d.getHours()).padStart(2, "0");
        const mm = String(d.getMinutes()).padStart(2, "0");
        return `${hh}:${mm}`;
      }

      function appendUserMessage(text: string) {
        hideEmptyState();
        const wrap = ownerDoc.createElement("div");
        wrap.style.display = "flex";
        wrap.style.flexDirection = "column";
        wrap.style.alignItems = "flex-end";
        wrap.style.gap = "4px";
        wrap.style.minWidth = "0";
        wrap.style.maxWidth = "100%";

        const bubble = ownerDoc.createElement("div");
        bubble.style.maxWidth = "88%";
        bubble.style.minWidth = "0";
        bubble.style.alignSelf = "flex-end";
        bubble.style.padding = "10px 12px";
        bubble.style.borderRadius = "10px";
        bubble.style.whiteSpace = "pre-wrap";
        bubble.style.wordBreak = "break-word";
        (bubble.style as any).overflowWrap = "anywhere";
        bubble.style.background = "rgba(60, 130, 255, 0.85)";
        bubble.style.color = "white";
        bubble.style.userSelect = "text";
        (bubble.style as any).MozUserSelect = "text";
        bubble.style.cursor = "text";
        bubble.textContent = text;

        const meta = ownerDoc.createElement("div");
        meta.style.fontSize = "11px";
        meta.style.opacity = "0.55";
        meta.textContent = nowHM();

        wrap.append(bubble, meta);
        conversationArea.appendChild(wrap);
        scrollConversationToBottom(true);
        return { wrap, bubble };
      }

      function splitThinkingAndAnswer(full: string): { thinking: string; answer: string } {
        const text = (full || "").replace(/\r\n/g, "\n");
        // Prefer explicit tags if present（须与后端 helpers.strip_think 一致）。
        const tag = text.match(/<think>([\s\S]*?)<\/redacted_thinking>/i);
        if (tag) {
          const thinking = String(tag[1] || "").trim();
          const answer = text.replace(tag[0], "").trim();
          return { thinking, answer };
        }
        // Heuristic: a "Thinking" section at the top.
        const lines = text.split("\n");
        const thinkingIdx = lines.findIndex((l) => l.trim().toLowerCase() === "thinking");
        if (thinkingIdx >= 0) {
          const after = lines.slice(thinkingIdx + 1);
          const blank = after.findIndex((l) => l.trim() === "");
          const thinkingLines = blank >= 0 ? after.slice(0, blank) : after;
          const rest = blank >= 0 ? after.slice(blank + 1) : [];
          const thinking = thinkingLines.join("\n").trim();
          const answer = rest.join("\n").trim() || lines.slice(0, thinkingIdx).join("\n").trim();
          return { thinking, answer: answer || text.trim() };
        }
        // Streaming case: <think> started but not yet closed.
        const openIdx = text.search(/<think>/i);
        if (openIdx >= 0) {
          const after = text.slice(openIdx).replace(/<think>/i, "");
          return { thinking: after.trim(), answer: text.slice(0, openIdx).trim() };
        }
        return { thinking: "", answer: text.trim() };
      }

      function formatThinkingParagraphs(raw: string): string {
        const text = (raw || "").replace(/\r\n/g, "\n").trim();
        if (!text) {
          return "";
        }
        const paragraphs = text
          .split(/\n{2,}/)
          .map((p) => p.trim())
          .filter((p) => !!p);
        return paragraphs
          .map((p) => {
            const normalized = p.replace(/\n+/g, "\n");
            if (normalized.startsWith("　　")) {
              return normalized;
            }
            return `　　${normalized}`;
          })
          .join("\n");
      }

      const KATEX_LINK_ID = "ff-llm-katex-stylesheet";
      const MD_WRAP_STYLE_ID = "ff-llm-md-wrap-style";

      function ensureKatexStylesheet(doc: Document | null | undefined) {
        if (!doc?.head) return;
        if (doc.getElementById(KATEX_LINK_ID)) return;
        const link = doc.createElement("link");
        link.id = KATEX_LINK_ID;
        link.rel = "stylesheet";
        link.href = `chrome://${config.addonRef}/content/katex.min.css`;
        doc.head.appendChild(link);
      }

      function ensureLlMarkdownWrapStyles(doc: Document | null | undefined) {
        if (!doc?.head) return;
        let style = doc.getElementById(MD_WRAP_STYLE_ID) as HTMLStyleElement | null;
        if (!style) {
          style = doc.createElement("style");
          style.id = MD_WRAP_STYLE_ID;
          doc.head.appendChild(style);
        }
        style.textContent = `
.ff-llm-md { max-width: 100%; min-width: 0; word-break: break-word; overflow-wrap: anywhere; }
.ff-llm-md pre { max-width: 100%; overflow-x: auto; box-sizing: border-box; }
.ff-llm-md .math-display { max-width: 100%; overflow-x: auto; box-sizing: border-box; }
.ff-llm-md .math-display-inline { max-width: 100%; display: inline-block; overflow-x: auto; vertical-align: middle; }
.ff-llm-md table { border-collapse: collapse; max-width: 100%; }
.ff-llm-md th, .ff-llm-md td { border: 1px solid rgba(127,127,127,0.35); padding: 4px 8px; }
.ff-llm-md ul, .ff-llm-md ol { margin: 4px 0; padding-left: 1.35em; }
.ff-llm-md blockquote { margin: 6px 0; padding-left: 8px; border-left: 2px solid rgba(127,127,127,0.35); opacity: 0.95; }
`;
      }

      /** 与旧 renderMarkdownLite 一致：笔误替换 + 行首全角空格归一（便于列表/结构解析）。 */
      function preprocessAssistantMarkdown(raw: string): string {
        let src = String(raw || "").replace(/\r\n/g, "\n").trim();
        if (!src) return "";
        src = src.replace(/折扣因子y(?=[（，、：])/g, "折扣因子γ");
        src = src.replace(/因子y(?=[（，、：])/g, "因子γ");
        return src.split("\n").map((ln) => ln.replace(/\u3000/g, "  ")).join("\n");
      }

      /**
       * 发送一轮流式对话（与输入框发送共用）。删除一轮后再次调用可基于最新会话重建上下文。
       */
      async function runSendPipeline(opts: {
        message: string;
        mediaPaths: string[];
        userDisplayText: string;
      }): Promise<void> {
        const { message, mediaPaths, userDisplayText } = opts;
        if (!String(message || "").trim() && mediaPaths.length === 0) {
          appendBubble("system", "无法发送：提问为空");
          return;
        }
        syncSendButtonState(true);
        try {
          const contextPayload = buildContextPayload();
          const baseMessage = contextPayload ? `${contextPayload}${message}` : message;
          let messageWithContext = baseMessage || (mediaPaths.length > 0 ? "[Image Context Attached]" : "");
          try {
            const resolved = await resolveWikiPdfInfo({ preferredItem: item });
            const currentWikiPdfPath = resolved.ok
              ? String(resolved.data.wikiPdfPath || "").trim()
              : "";
            if (currentWikiPdfPath) {
              messageWithContext = `[zotero_current_wiki_pdf_path=${currentWikiPdfPath}]\n${messageWithContext}`;
            }
          } catch {
            // ignore
          }
          const literatureTitle = resolveCurrentLiteratureTitle();
          appendUserMessage(userDisplayText);
          const userRecord: ChatRecord = { role: "user", content: userDisplayText };
          if (literatureTitle) {
            userRecord.literature_title = literatureTitle;
          }
          getTabHistory(activeTabId).push(userRecord);
          activeStreamAbortController = createAbortControllerCompat();
          ztoolkit.log("[llm-ui] sending:", messageWithContext);
          try {
            await ensureFireFlyBridgeStarted();
            const streamSessionID = getActiveSessionID();
            const assistant = appendAssistantShell(currentModelLabel);
            let streamedText = "";
            let streamedThinking = "";
            let allowAnswerStream = false;
            await streamFromFireFly(
              messageWithContext,
              streamSessionID,
              mediaPaths,
              (delta) => {
                if (!allowAnswerStream) {
                  return;
                }
                streamedText += delta;
                const parsedLive = splitThinkingAndAnswer(streamedText);
                assistant.setAnswerText(sanitizeAssistantText(parsedLive.answer || ""));
                scrollConversationToBottom();
              },
              (finalContent) => {
                if (finalContent) {
                  if (!streamedText.trim()) {
                    streamedText = finalContent;
                  }
                  assistant.setAnswerText(
                    sanitizeAssistantText(splitThinkingAndAnswer(streamedText).answer || ""),
                  );
                  scrollConversationToBottom();
                }
              },
              (thinkingDelta) => {
                streamedThinking += thinkingDelta;
                assistant.appendThinkingDelta(thinkingDelta);
                scrollConversationToBottom();
              },
              (toolStep) => {
                assistant.appendToolStep(toolStep);
                scrollConversationToBottom();
              },
              (_resuming) => {
                if (_resuming === false) {
                  allowAnswerStream = true;
                }
                scrollConversationToBottom();
              },
              activeStreamAbortController.signal,
              literatureTitle,
            );
            const parsed = splitThinkingAndAnswer(streamedText);
            const mergedThinking = [streamedThinking, parsed.thinking]
              .filter((s) => !!s && s.trim())
              .join("\n\n")
              .trim();
            assistant.finalizeProgressThinking(mergedThinking);
            if (parsed.answer) {
              assistant.setAnswerText(sanitizeAssistantText(parsed.answer));
            }
            if (!streamedText.trim()) {
              assistant.setAnswerText("(无输出)");
            }
            const finalParsed = splitThinkingAndAnswer(streamedText);
            const cleanAnswer = sanitizeAssistantText(
              finalParsed.answer ||
                String((assistant as any).answerMdSlot?.textContent || "").trim() ||
                String(assistant.answerBubble.textContent || "").trim(),
            );
            const completedAtIso = new Date().toISOString();
            const assistantRecord: ChatRecord = {
              role: "assistant",
              content: cleanAnswer || "(无输出)",
              reasoning_content: [streamedThinking, finalParsed.thinking]
                .filter((s) => !!s && s.trim())
                .join("\n\n"),
              turn_timestamp: completedAtIso,
            };
            if (literatureTitle) {
              assistantRecord.literature_title = literatureTitle;
            }
            assistant.setAssistantTurnTimeIso(completedAtIso);
            getTabHistory(activeTabId).push(assistantRecord);
            try {
              const sess = await fetchZoteroChatHistories(streamSessionID);
              const rows = sess[streamSessionID] || [];
              for (let i = rows.length - 1; i >= 0; i--) {
                const row = rows[i]!;
                if (row.role === "assistant" && typeof row.session_message_index === "number") {
                  assistant.setAssistantSessionMessageIndex(row.session_message_index);
                  assistantRecord.session_message_index = row.session_message_index;
                  if (row.timestamp) {
                    const ts = String(row.timestamp);
                    assistantRecord.turn_timestamp = ts;
                    assistant.setAssistantTurnTimeIso(ts);
                  }
                  break;
                }
              }
            } catch (e2) {
              ztoolkit.log("[llm-ui] attach session_message_index failed:", String(e2));
            }
          } catch (e) {
            const msg = String((e as any)?.message || e || "");
            const aborted =
              msg.toLowerCase().includes("abort") ||
              msg.toLowerCase().includes("aborted") ||
              msg.toLowerCase().includes("cancel");
            if (aborted) {
              appendBubble("system", "已取消发送");
              ztoolkit.log("[llm-ui] send canceled");
            } else {
              appendBubble("system", `发送失败: ${msg}`);
              ztoolkit.log("[llm-ui] send failed:", msg);
            }
          }
        } finally {
          activeStreamAbortController = null;
          syncSendButtonState(false);
          await refreshBridgeStatus();
        }
      }

      /** 本地时间 ``yy/mm/dd  hh:mm``（日与时刻之间两个空格） */
      function formatAssistantTurnTime(iso: string): string {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return "";
        const yy = String(d.getFullYear()).slice(-2);
        const mm = String(d.getMonth() + 1).padStart(2, "0");
        const dd = String(d.getDate()).padStart(2, "0");
        const hh = String(d.getHours()).padStart(2, "0");
        const mi = String(d.getMinutes()).padStart(2, "0");
        return `${yy}/${mm}/${dd}  ${hh}:${mi}`;
      }

      function appendAssistantShell(modelLabelText: string) {
        hideEmptyState();
        // Inject keyframes once per document for typing dots.
        const typingStyleId = "ff-llm-typing-style";
        if (!ownerDoc.getElementById(typingStyleId)) {
          const style = ownerDoc.createElement("style");
          style.id = typingStyleId;
          style.textContent = `
@keyframes ffTypingDot {
  0%, 100% { transform: translateY(0) scale(0.45); opacity: 0.35; }
  40% { transform: translateY(-5px) scale(1.22); opacity: 1; }
  70% { transform: translateY(-1px) scale(0.78); opacity: 0.65; }
}
`;
          ownerDoc.head?.appendChild(style);
        }
        const wrap = ownerDoc.createElement("div");
        wrap.style.display = "flex";
        wrap.style.flexDirection = "column";
        wrap.style.alignItems = "flex-start";
        wrap.style.gap = "6px";
        wrap.style.maxWidth = "96%";
        wrap.style.minWidth = "0";
        wrap.style.width = "100%";
        wrap.style.boxSizing = "border-box";

        const modelLabel = ownerDoc.createElement("div");
        modelLabel.style.fontSize = "12px";
        modelLabel.style.opacity = "0.55";
        modelLabel.style.fontWeight = "600";
        modelLabel.textContent = modelLabelText;

        const progressLane = ownerDoc.createElement("div");
        progressLane.style.display = "none";
        progressLane.style.flexDirection = "column";
        progressLane.style.gap = "10px";
        progressLane.style.width = "100%";
        progressLane.style.minWidth = "0";
        progressLane.style.maxWidth = "100%";

        type ProgressSegment = {
          root: HTMLDivElement;
          thinkingWrap: HTMLDivElement;
          thinkingBody: HTMLDivElement;
          stepsWrap: HTMLDivElement;
          thinkingBuf: string;
          hasToolSteps: boolean;
          setThinkingOpen: (open: boolean) => void;
        };

        let currentSegment: ProgressSegment | null = null;
        const progressSegments: ProgressSegment[] = [];

        const formatToolStepLabel = (raw: string): string => {
          const text = String(raw || "").trim();
          if (!text) return "";
          if (text.startsWith("$ ")) return text;
          return text.charAt(0).toUpperCase() + text.slice(1);
        };

        /** 按顶层逗号拆分多条工具提示，忽略引号内的逗号。 */
        const splitToolHintLines = (hint: string): string[] => {
          const parts: string[] = [];
          let buf = "";
          let depth = 0;
          let inString = false;
          let quoteChar = "";
          let escaped = false;
          for (let i = 0; i < hint.length; i++) {
            const ch = hint[i]!;
            if (inString) {
              buf += ch;
              if (escaped) {
                escaped = false;
              } else if (ch === "\\") {
                escaped = true;
              } else if (ch === quoteChar) {
                inString = false;
              }
              continue;
            }
            if (ch === '"' || ch === "'") {
              inString = true;
              quoteChar = ch;
              buf += ch;
              continue;
            }
            if (ch === "(") {
              depth += 1;
              buf += ch;
              continue;
            }
            if (ch === ")") {
              depth = Math.max(0, depth - 1);
              buf += ch;
              continue;
            }
            if (ch === "," && depth === 0) {
              const next = hint[i + 1];
              if (next === " ") {
                const piece = buf.trim();
                if (piece) parts.push(piece);
                buf = "";
                i += 1;
                continue;
              }
            }
            buf += ch;
          }
          const tail = buf.trim();
          if (tail) parts.push(tail);
          return parts.length ? parts : [hint.trim()];
        };

        const appendStepRow = (stepsWrap: HTMLDivElement, label: string) => {
          const row = ownerDoc.createElement("div");
          row.textContent = label;
          row.style.fontSize = "12px";
          row.style.lineHeight = "1.55";
          row.style.opacity = "0.62";
          row.style.whiteSpace = "pre-wrap";
          row.style.wordBreak = "break-word";
          (row.style as any).overflowWrap = "anywhere";
          row.style.fontFamily =
            "'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif";
          stepsWrap.appendChild(row);
        };

        const createProgressSegment = (): ProgressSegment => {
          const root = ownerDoc.createElement("div");
          root.style.display = "flex";
          root.style.flexDirection = "column";
          root.style.gap = "4px";
          root.style.minWidth = "0";
          root.style.maxWidth = "100%";

          const thinkingWrap = ownerDoc.createElement("div");
          thinkingWrap.style.display = "none";

          const thinkingHead = ownerDoc.createElement("div");
          thinkingHead.style.display = "inline-flex";
          thinkingHead.style.alignItems = "center";
          thinkingHead.style.gap = "6px";
          thinkingHead.style.cursor = "pointer";
          thinkingHead.style.userSelect = "none";
          (thinkingHead.style as any).MozUserSelect = "none";

          const arrow = ownerDoc.createElement("span");
          arrow.textContent = "▼";
          arrow.style.opacity = "0.65";
          arrow.style.fontSize = "12px";

          const tTitle = ownerDoc.createElement("span");
          tTitle.textContent = "Thinking";
          tTitle.style.fontWeight = "600";
          tTitle.style.fontSize = "13px";

          thinkingHead.append(arrow, tTitle);

          const thinkingBody = ownerDoc.createElement("div");
          thinkingBody.style.marginTop = "4px";
          thinkingBody.style.marginLeft = "2px";
          thinkingBody.style.whiteSpace = "pre-wrap";
          thinkingBody.style.wordBreak = "break-word";
          (thinkingBody.style as any).overflowWrap = "anywhere";
          thinkingBody.style.fontSize = "13px";
          thinkingBody.style.lineHeight = "1.7";
          thinkingBody.style.opacity = "0.78";
          thinkingBody.style.fontFamily =
            "'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Segoe UI', sans-serif";
          thinkingBody.textContent = " ";

          let open = true;
          const setOpen = (v: boolean) => {
            open = v;
            thinkingBody.style.display = open ? "block" : "none";
            arrow.textContent = open ? "▼" : "▶";
          };
          thinkingHead.addEventListener("click", () => setOpen(!open));

          thinkingWrap.append(thinkingHead, thinkingBody);

          const stepsWrap = ownerDoc.createElement("div");
          stepsWrap.style.display = "none";
          stepsWrap.style.flexDirection = "column";
          stepsWrap.style.gap = "2px";
          stepsWrap.style.paddingLeft = "2px";

          root.append(thinkingWrap, stepsWrap);
          progressLane.appendChild(root);
          progressLane.style.display = "flex";

          return {
            root,
            thinkingWrap,
            thinkingBody,
            stepsWrap,
            thinkingBuf: "",
            hasToolSteps: false,
            setThinkingOpen: setOpen,
          };
        };

        const collapseSegmentThinking = (seg: ProgressSegment | null) => {
          if (!seg || !seg.thinkingBuf.trim()) return;
          seg.setThinkingOpen(false);
        };

        const startNewSegment = () => {
          collapseSegmentThinking(currentSegment);
          currentSegment = createProgressSegment();
          progressSegments.push(currentSegment);
        };

        const ensureSegment = () => {
          if (!currentSegment) {
            startNewSegment();
          }
          return currentSegment!;
        };

        const appendThinkingDelta = (delta: string) => {
          const chunk = String(delta || "");
          if (!chunk) return;
          if (currentSegment?.hasToolSteps) {
            startNewSegment();
          }
          const seg = ensureSegment();
          seg.thinkingBuf += chunk;
          seg.thinkingWrap.style.display = "block";
          seg.thinkingBody.textContent = formatThinkingParagraphs(seg.thinkingBuf);
          seg.setThinkingOpen(true);
          setTypingVisible(false);
        };

        const appendToolStep = (hint: string) => {
          const seg = ensureSegment();
          const hadThinking = !!seg.thinkingBuf.trim();
          const lines = splitToolHintLines(hint);
          for (const line of lines) {
            const label = formatToolStepLabel(line);
            if (!label) continue;
            appendStepRow(seg.stepsWrap, label);
          }
          if (seg.stepsWrap.childElementCount > 0) {
            seg.stepsWrap.style.display = "flex";
            if (hadThinking && !seg.hasToolSteps) {
              collapseSegmentThinking(seg);
            }
            seg.hasToolSteps = true;
            setTypingVisible(false);
          }
        };

        const finalizeProgressThinking = (fallbackThinking: string) => {
          const tail = String(fallbackThinking || "").trim();
          if (tail && currentSegment && !currentSegment.thinkingBuf.trim()) {
            currentSegment.thinkingBuf = tail;
            currentSegment.thinkingWrap.style.display = "block";
            currentSegment.thinkingBody.textContent = formatThinkingParagraphs(tail);
            progressLane.style.display = "flex";
          }
          if (!progressLane.childElementCount && tail) {
            startNewSegment();
            currentSegment!.thinkingBuf = tail;
            currentSegment!.thinkingWrap.style.display = "block";
            currentSegment!.thinkingBody.textContent = formatThinkingParagraphs(tail);
          }
          if (!progressLane.childElementCount) {
            progressLane.style.display = "none";
          } else {
            for (let i = 0; i < progressSegments.length - 1; i++) {
              collapseSegmentThinking(progressSegments[i]!);
            }
            if (currentSegment) {
              collapseSegmentThinking(currentSegment);
            }
          }
        };

        const setThinking = (t: string, options?: { clearWhenEmpty?: boolean }) => {
          const clearWhenEmpty = Boolean(options?.clearWhenEmpty);
          const val = (t || "").trim();
          progressLane.replaceChildren();
          progressSegments.length = 0;
          currentSegment = null;
          if (!val) {
            if (clearWhenEmpty) {
              progressLane.style.display = "none";
            }
            return;
          }
          startNewSegment();
          currentSegment!.thinkingBuf = val;
          currentSegment!.thinkingWrap.style.display = "block";
          currentSegment!.thinkingBody.textContent = formatThinkingParagraphs(val);
          currentSegment!.setThinkingOpen(false);
          setTypingVisible(false);
        };

        const answerBubble = ownerDoc.createElement("div");
        answerBubble.style.padding = "10px 12px";
        answerBubble.style.borderRadius = "10px";
        answerBubble.style.minWidth = "0";
        answerBubble.style.maxWidth = "100%";
        answerBubble.style.width = "100%";
        answerBubble.style.alignSelf = "stretch";
        answerBubble.style.boxSizing = "border-box";
        answerBubble.style.display = "flex";
        answerBubble.style.flexDirection = "column";
        answerBubble.style.alignItems = "stretch";
        answerBubble.style.gap = "0";
        answerBubble.style.whiteSpace = "normal";
        answerBubble.style.wordBreak = "break-word";
        (answerBubble.style as any).overflowWrap = "anywhere";
        answerBubble.style.background = "transparent";
        answerBubble.style.fontSize = "15px";
        answerBubble.style.lineHeight = "1.72";
        answerBubble.style.fontFamily =
          "'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Segoe UI', sans-serif";
        answerBubble.style.userSelect = "text";
        (answerBubble.style as any).MozUserSelect = "text";
        answerBubble.style.cursor = "text";
        answerBubble.style.color = "inherit";
        answerBubble.style.fontFamily =
          "'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Segoe UI', 'Segoe UI Symbol', sans-serif";

        const mdSlot = ownerDoc.createElement("div");
        mdSlot.style.minWidth = "0";
        mdSlot.style.maxWidth = "100%";

        const typingDots = ownerDoc.createElement("div");
        typingDots.style.display = "inline-flex";
        typingDots.style.alignItems = "center";
        typingDots.style.gap = "6px";
        typingDots.style.height = "22px";
        typingDots.style.padding = "2px 0";
        typingDots.style.opacity = "0.9";
        for (let i = 0; i < 3; i++) {
          const dot = ownerDoc.createElement("span");
          dot.style.width = "6px";
          dot.style.height = "6px";
          dot.style.borderRadius = "999px";
          dot.style.background = "currentColor";
          dot.style.display = "inline-block";
          dot.style.transformOrigin = "50% 50%";
          (dot.style as any).MozTransformOrigin = "50% 50%";
          dot.style.willChange = "transform, opacity";
          dot.style.animation = `ffTypingDot 0.95s ${i * 0.16}s infinite cubic-bezier(0.45, 0.05, 0.55, 0.95)`;
          typingDots.appendChild(dot);
        }
        mdSlot.appendChild(typingDots);

        const deleteRow = ownerDoc.createElement("div");
        deleteRow.style.display = "none";
        deleteRow.style.width = "100%";
        deleteRow.style.maxWidth = "100%";
        deleteRow.style.alignSelf = "stretch";
        deleteRow.style.marginTop = "4px";
        deleteRow.style.flexShrink = "0";

        /** 时间与图标同一套深浅：统一在这一层透明度，避免文字继承正文色导致比图标更深 */
        const footerInner = ownerDoc.createElement("div");
        footerInner.style.display = "flex";
        footerInner.style.flexDirection = "row";
        footerInner.style.justifyContent = "space-between";
        footerInner.style.alignItems = "center";
        footerInner.style.gap = "8px";
        footerInner.style.width = "100%";
        footerInner.style.opacity = "0.72";
        footerInner.style.fontSize = "11px";
        footerInner.style.lineHeight = "1.3";

        const timeLabel = ownerDoc.createElement("span");
        timeLabel.style.flex = "1";
        timeLabel.style.minWidth = "0";
        timeLabel.style.whiteSpace = "nowrap";
        timeLabel.style.overflow = "hidden";
        timeLabel.style.textOverflow = "ellipsis";
        timeLabel.style.color = "rgb(75, 75, 75)";
        timeLabel.textContent = "";

        const delBtn = ownerDoc.createElement("button");
        delBtn.type = "button";
        delBtn.style.display = "inline-flex";
        delBtn.style.alignItems = "center";
        delBtn.style.justifyContent = "center";
        delBtn.style.padding = "2px";
        delBtn.style.borderRadius = "6px";
        delBtn.style.border = "none";
        delBtn.style.background = "transparent";
        delBtn.style.color = "inherit";
        delBtn.style.cursor = "pointer";
        delBtn.style.transition = "background 120ms ease";
        delBtn.title = "从会话文件中删除本条助手回复及对应的用户提问";
        delBtn.addEventListener("mouseenter", () => {
          delBtn.style.background = "rgba(127, 127, 127, 0.16)";
        });
        delBtn.addEventListener("mouseleave", () => {
          delBtn.style.background = "transparent";
        });
        const delIcon = ownerDoc.createElement("img");
        delIcon.src = `${iconBase}/delete_session.svg`;
        delIcon.alt = "";
        delIcon.style.width = "18px";
        delIcon.style.height = "18px";
        delIcon.style.display = "block";
        delIcon.style.pointerEvents = "none";
        delBtn.appendChild(delIcon);

        const rightPack = ownerDoc.createElement("span");
        rightPack.style.display = "inline-flex";
        rightPack.style.alignItems = "center";
        rightPack.style.gap = "4px";
        rightPack.style.flexShrink = "0";

        const resetBtn = ownerDoc.createElement("button");
        resetBtn.type = "button";
        resetBtn.style.display = "none";
        resetBtn.style.alignItems = "center";
        resetBtn.style.justifyContent = "center";
        resetBtn.style.padding = "2px";
        resetBtn.style.borderRadius = "6px";
        resetBtn.style.border = "none";
        resetBtn.style.background = "transparent";
        resetBtn.style.color = "inherit";
        resetBtn.style.cursor = "pointer";
        resetBtn.style.transition = "background 120ms ease";
        resetBtn.title = "删除本条对话并以同一问题重新发送（服务端会话已删该轮，上下文重新构建）";
        resetBtn.addEventListener("mouseenter", () => {
          resetBtn.style.background = "rgba(127, 127, 127, 0.16)";
        });
        resetBtn.addEventListener("mouseleave", () => {
          resetBtn.style.background = "transparent";
        });
        const resetIcon = ownerDoc.createElement("img");
        resetIcon.src = `${iconBase}/Reset.svg`;
        resetIcon.alt = "";
        resetIcon.style.width = "18px";
        resetIcon.style.height = "18px";
        resetIcon.style.display = "block";
        resetIcon.style.pointerEvents = "none";
        resetBtn.appendChild(resetIcon);

        rightPack.append(resetBtn, delBtn);
        footerInner.append(timeLabel, rightPack);
        deleteRow.appendChild(footerInner);
        answerBubble.append(mdSlot, deleteRow);

        const setTypingVisible = (visible: boolean) => {
          typingDots.style.display = visible ? "inline-flex" : "none";
          if (visible) {
            answerBubble.style.background = "transparent";
          }
        };
        const setAnswerText = (text: string) => {
          const val = String(text || "");
          const has = !!val.trim();
          setTypingVisible(!has);
          if (has) {
            ensureKatexStylesheet(ownerDoc);
            ensureLlMarkdownWrapStyles(ownerDoc);
            try {
              mdSlot.innerHTML = `<div class="ff-llm-md">${renderMarkdown(preprocessAssistantMarkdown(val))}</div>`;
            } catch (e) {
              ztoolkit.log("[llm-ui] renderMarkdown failed:", String(e));
              const esc = String(val || "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");
              mdSlot.innerHTML = `<div class="ff-llm-md"><pre style="white-space:pre-wrap;">${esc}</pre></div>`;
            }
          } else {
            mdSlot.textContent = " ";
            mdSlot.appendChild(typingDots);
          }
          answerBubble.style.background = "transparent";
        };

        let assistantSessionMessageIndex: number | null = null;
        let assistantTurnTimeFormatted = "";

        const syncAssistantFooterRow = () => {
          const hasDel = assistantSessionMessageIndex != null;
          const hasTime = !!assistantTurnTimeFormatted.trim();
          deleteRow.style.display = hasDel || hasTime ? "flex" : "none";
          timeLabel.textContent = assistantTurnTimeFormatted;
          resetBtn.style.display = hasDel ? "inline-flex" : "none";
          delBtn.style.display = hasDel ? "inline-flex" : "none";
        };

        const setAssistantTurnTimeIso = (iso: string | null | undefined) => {
          assistantTurnTimeFormatted = iso ? formatAssistantTurnTime(iso) : "";
          syncAssistantFooterRow();
        };

        const setAssistantSessionMessageIndex = (idx: number | null) => {
          assistantSessionMessageIndex = typeof idx === "number" && idx >= 0 ? idx : null;
          syncAssistantFooterRow();
        };

        delBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (assistantSessionMessageIndex === null) return;
          void (async () => {
            const sid = getActiveSessionID();
            try {
              await ensureFireFlyBridgeStarted();
              const r = await deleteZoteroConversationTurn(sid, assistantSessionMessageIndex);
              if (!r.ok) {
                appendBubble("system", `删除失败：${r.detail || "unknown"}`);
                return;
              }
              removeLocalTurnByAssistantIndex(activeTabId, assistantSessionMessageIndex);
              renderActiveTabConversation();
            } catch (e) {
              appendBubble("system", `删除失败：${String((e as any)?.message || e || "")}`);
            }
          })();
        });

        resetBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (isSending) {
            appendBubble("system", "请等待当前回复结束后再重置");
            return;
          }
          if (assistantSessionMessageIndex === null) return;
          const idx = assistantSessionMessageIndex;
          void (async () => {
            const arr = getTabHistory(activeTabId);
            const aPos = arr.findIndex(
              (r) => r.role === "assistant" && r.session_message_index === idx,
            );
            if (aPos < 1 || arr[aPos - 1]!.role !== "user") {
              appendBubble("system", "无法重置：找不到对应的提问");
              return;
            }
            const paired = arr[aPos - 1]!;
            const userDisplayText = paired.content;
            const message = normalizeDisplayText(paired.content).trim();
            if (!message) {
              appendBubble(
                "system",
                "无法重置：原提问无文本（纯图片或未保存内容时请删除后手动重发）",
              );
              return;
            }
            try {
              await ensureFireFlyBridgeStarted();
              const sid = getActiveSessionID();
              const r = await deleteZoteroConversationTurn(sid, idx);
              if (!r.ok) {
                appendBubble("system", `重置失败：${r.detail || "unknown"}`);
                return;
              }
              removeLocalTurnByAssistantIndex(activeTabId, idx);
              renderActiveTabConversation();
              await runSendPipeline({
                message,
                mediaPaths: [],
                userDisplayText,
              });
            } catch (e) {
              appendBubble("system", `重置失败：${String((e as any)?.message || e || "")}`);
            }
          })();
        });

        wrap.append(modelLabel, progressLane, answerBubble);
        conversationArea.appendChild(wrap);
        scrollConversationToBottom();

        let hasThinkingContent = false;
        const setThinkingLegacy = setThinking;

        return {
          wrap,
          answerBubble,
          answerMdSlot: mdSlot,
          progressLane,
          setThinking: (t: string, options?: { clearWhenEmpty?: boolean }) => {
            const val = (t || "").trim();
            hasThinkingContent = !!val;
            setThinkingLegacy(t, options);
          },
          appendThinkingDelta,
          appendToolStep,
          finalizeProgressThinking,
          setAnswerText,
          setTypingVisible,
          setAssistantSessionMessageIndex,
          setAssistantTurnTimeIso,
        };
      }

      function normalizeDisplayText(text: string) {
        let normalized = String(text || "");
        // 去除后端注入的条目上下文标记。
        normalized = normalized.replace(/^\[zotero_current_item_id=\d+\]\s*\n?/m, "");
        // 去除前端附加给后端的当前文献路径标记（任意行首出现）。
        normalized = normalized.replace(/^\[zotero_current_wiki_pdf_path=.*?\]\s*\n?/gm, "");
        // 去除仅用于检索的 RAG 注入块，避免恢复历史时污染用户可读内容。
        normalized = normalized.replace(/\[RAG Context\][\s\S]*?\[\/RAG Context\]\s*\n*/g, "");
        // 去除可能残留的文本上下文包裹标签，仅保留用户输入主问题。
        normalized = normalized.replace(/\[Text Context\][\s\S]*?\[\/Text Context\]\s*\n*/g, "");
        // 去除 bridge 在 RAG 后追加的「再确认 / 请直接回答」整段（旧会话里可能已写入 jsonl）。
        normalized = normalized.replace(/\n----\n【RAG 再确认】[^\n]*(\n|$)/g, "");
        normalized = normalized.replace(/\n----\n【请直接回答此问（优先于旧对话）】[\s\S]*$/m, "");
        return normalized.trim();
      }

      /** 去掉终端 Rich 等产生的 ANSI/CSI 序列，避免在 Zotero 面板里显示为乱码或污染 Markdown。 */
      function stripTerminalAnsi(raw: string): string {
        let s = String(raw || "");
        // CSI：ESC [ … 末字节 @–~（含 truecolor 等）
        s = s.replace(/\u001b\[[0-?]*[-/]*[@-~]/g, "");
        // OSC：ESC ] … BEL 或 ST
        s = s.replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, "");
        // 其它单字节 ESC 序列
        s = s.replace(/\u001b[@-Z\\-_]/g, "");
        // 丢失 ESC 时常见 “?” + CSI 残留
        s = s.replace(/\?\[[0-?]*[-/]*[@-~]/g, "");
        return s;
      }

      function removeLocalTurnByAssistantIndex(tabId: number, assistantMessageIndex: number): boolean {
        const arr = getTabHistory(tabId);
        const i = arr.findIndex(
          (r) => r.role === "assistant" && r.session_message_index === assistantMessageIndex,
        );
        if (i < 0) {
          return false;
        }
        const hasUserBefore = i > 0 && arr[i - 1]!.role === "user";
        const start = hasUserBefore ? i - 1 : i;
        arr.splice(start, i - start + 1);
        return true;
      }

      function sanitizeAssistantText(text: string): string {
        let sanitized = stripTerminalAnsi(normalizeDisplayText(text));
        // 去除模型偶发输出的“技术解释行”，避免把 RAG 实现细节暴露给用户。
        const noisyLinePatterns = [
          /.*从\s*RAG\s*上下文可见.*$/gim,
          /.*根据\s*RAG\s*(上下文|检索|片段).*$\n?/gim,
          /.*以下(?:内容|片段).*来自.*RAG.*$/gim,
          /.*source:\s*.*$/gim,
          /.*检索片段.*$/gim,
          /.*RAG\s*Context.*$/gim,
        ];
        for (const pattern of noisyLinePatterns) {
          sanitized = sanitized.replace(pattern, "");
        }
        sanitized = sanitized.replace(/\n{3,}/g, "\n\n").trim();
        return sanitized;
      }

      function renderActiveTabConversation() {
        conversationArea.replaceChildren();
        emptyStateEl = null;
        const records = getTabHistory(activeTabId);
        if (!records.length) {
          showEmptyState();
          return;
        }
        for (const record of records) {
          if (record.role === "user") {
            appendUserMessage(normalizeDisplayText(record.content));
            continue;
          }
          const assistant = appendAssistantShell(currentModelLabel);
          const parsed = splitThinkingAndAnswer(record.content || "");
          const mergedThinking = [record.reasoning_content || "", parsed.thinking]
            .filter((s) => !!s && s.trim())
            .join("\n\n")
            .trim();
          assistant.setThinking(mergedThinking);
          assistant.setAnswerText(
            sanitizeAssistantText(parsed.answer || normalizeDisplayText(record.content) || " ") || " ",
          );
          if (record.turn_timestamp) {
            assistant.setAssistantTurnTimeIso(record.turn_timestamp);
          }
          if (typeof record.session_message_index === "number" && record.session_message_index >= 0) {
            assistant.setAssistantSessionMessageIndex(record.session_message_index);
          }
        }
      }

      async function restoreChatHistories() {
        try {
          await ensureFireFlyBridgeStarted();
          const sessions = await fetchZoteroChatHistories();
          for (let tabId = 1; tabId <= MAX_CHAT_TABS; tabId++) {
            const key = getSessionIDByTab(tabId);
            const rows = Array.isArray((sessions as any)[key]) ? (sessions as any)[key] : [];
            const restored = rows
              .filter((row: any) => row && (row.role === "user" || row.role === "assistant"))
              .map(
                (row: any) =>
                  ({
                    role: row.role,
                    content: String(row.content || ""),
                    reasoning_content: String(row.reasoning_content || ""),
                    literature_title: String(row.literature_title || "").trim() || undefined,
                    session_message_index:
                      typeof row.session_message_index === "number" && row.session_message_index >= 0
                        ? row.session_message_index
                        : undefined,
                    turn_timestamp:
                      row.role === "assistant" && row.timestamp
                        ? String(row.timestamp)
                        : undefined,
                  }) as ChatRecord,
              );
            chatHistoryByTab.set(tabId, restored);
            if (tabId === 1) {
              continue;
            }
            const hasHistory = restored.length > 0;
            const hasTab = chatTabs.some((t) => t.id === tabId);
            if (hasHistory && !hasTab) {
              createTab(tabId);
            } else if (!hasHistory && hasTab) {
              // 若对应会话文件不存在/为空，则不展示该标签。
              closeTab(tabId);
            }
          }
          renderActiveTabConversation();
          syncAddTabButtonState();
        } catch (e) {
          ztoolkit.log("[llm-ui] restore history failed:", String(e));
          renderActiveTabConversation();
        }
      }

      async function refreshBridgeStatus() {
        const ok = await isBridgeHealthy();
        bridgeStatusText.textContent = ok ? "FireFly online" : "FireFly offline";
        bridgeDot.style.background = ok ? "rgba(34, 160, 90, 0.95)" : "rgba(200, 80, 65, 0.95)";
        bridgeStatus.style.borderColor = ok
          ? "rgba(34, 160, 90, 0.35)"
          : "rgba(200, 80, 65, 0.35)";
        bridgeStatus.style.background = ok
          ? "rgba(34, 160, 90, 0.10)"
          : "rgba(200, 80, 65, 0.10)";
        bridgeStatusText.style.color = "";
      }

      async function sendCurrentMessage() {
        if (isSending) {
          return;
        }
        const message = textArea.value.trim();
        const mediaPaths = imageContextValues.map((v) => v.path).filter((v) => !!v);
        if (!message && mediaPaths.length === 0) {
          return;
        }
        // 必须在任意 await 之前清空输入并占用发送位：否则同一 click 上绑定的多个处理器
        //（如 addEventListener + onclick）会在首个 await 让出后各跑一轮，造成重复气泡与重复请求。
        textArea.value = "";
        const userDisplayText =
          message || (mediaPaths.length > 0 ? `[已附带 ${mediaPaths.length} 张图片]` : "(空消息)");
        await runSendPipeline({ message, mediaPaths, userDisplayText });
      }

      // 防止 item pane 上层事件抢占点击，确保按钮动作能触发。
      sendBtn.addEventListener("mousedown", (ev) => {
        ev.stopPropagation();
      });
      sendBtn.addEventListener(
        "click",
        () => {
          if (isSending) {
            void cancelActiveGeneration();
            return;
          }
          void sendCurrentMessage();
        },
        { capture: true },
      );
      textArea.addEventListener("keydown", (ev: KeyboardEvent) => {
        if (ev.key === "Enter" && !ev.shiftKey) {
          if (ev.repeat) {
            return;
          }
          ev.preventDefault();
          if (!isSending) {
            void sendCurrentMessage();
          }
        }
      });

      void refreshBridgeStatus();
      if (w) {
        bridgeHealthTimer = w.setInterval(() => {
          void refreshBridgeStatus();
        }, 3000);
      }

      root.append(topBar, conversationArea, composeCard);
      (body as any).replaceChildren?.(root);
      void restoreChatHistories();
    },
  });
}

