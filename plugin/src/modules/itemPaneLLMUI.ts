import { getLocaleID } from "../utils/locale";
import {
  ensureNanobotBridgeStarted,
  fetchZoteroChatHistories,
  isBridgeHealthy,
  streamFromNanobot,
} from "./nanobotBridge";
import { config } from "../../package.json";

let bridgeHealthTimer: number | null = null;

/**
 * 独立的 Item Pane LLM 页面模块。
 * 负责将提问发送到本地 nanobot bridge（/zotero/message）。
 */
export function registerLLMItemPaneSection() {
  Zotero.ItemPaneManager.registerSection({
    paneID: "llm-ui",
    pluginID: addon.data.config.addonID,
    header: {
      l10nID: getLocaleID("item-section-example1-head-text"),
      icon: `chrome://${config.addonRef}/content/icons/FireFly-head.svg`,
    },
    sidenav: {
      l10nID: getLocaleID("item-section-example1-sidenav-tooltip"),
      icon: `chrome://${config.addonRef}/content/icons/FireFly-head.svg`,
    },
    onRender: ({ body }) => {
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
      bodyEl.style.display = "flex";
      bodyEl.style.flexDirection = "column";

      const root = ownerDoc.createElement("div");
      root.style.display = "flex";
      root.style.flexDirection = "column";
      root.style.gap = "10px";
      root.style.padding = "10px 0";
      root.style.height = "100%";
      // Zotero pane 里部分容器默认不可选中，显式允许文本选择/复制。
      root.style.userSelect = "text";
      (root.style as any).MozUserSelect = "text";

      const topBar = ownerDoc.createElement("div");
      topBar.style.display = "flex";
      topBar.style.justifyContent = "center";
      topBar.style.alignItems = "center";
      topBar.style.gap = "6px";

      type TabState = {
        id: number;
        chip: HTMLDivElement;
        labelBtn: HTMLButtonElement;
        closeBtn: HTMLButtonElement;
      };
      type ChatRecord = {
        role: "user" | "assistant";
        content: string;
        reasoning_content?: string;
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

      topBar.append(tabBar, addTabBtn);

      // 对话区域
      const conversationArea = ownerDoc.createElement("div");
      conversationArea.style.flex = "1";
      conversationArea.style.minHeight = "180px";
      conversationArea.style.border = "1px solid var(--color-border)";
      conversationArea.style.borderRadius = "10px";
      conversationArea.style.padding = "10px";
      conversationArea.style.overflowY = "auto";
      conversationArea.style.userSelect = "text";
      (conversationArea.style as any).MozUserSelect = "text";
      conversationArea.style.fontSize = "13px";
      conversationArea.style.display = "flex";
      conversationArea.style.flexDirection = "column";
      conversationArea.style.gap = "8px";
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
        title.style.fontSize = "24px";
        title.style.fontWeight = "700";
        title.style.lineHeight = "1.18";
        title.style.letterSpacing = "0.01em";

        const sub = ownerDoc.createElement("div");
        sub.textContent = "和流萤一起，探索整个Paper";
        sub.style.marginTop = "8px";
        sub.style.fontSize = "18px";
        sub.style.opacity = "0.52";
        sub.style.fontStyle = "italic";
        sub.style.lineHeight = "1.36";

        box.append(title, sub);
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
      composeCard.style.padding = "10px";
      composeCard.style.display = "flex";
      composeCard.style.flexDirection = "column";
      composeCard.style.gap = "10px";
      composeCard.style.background = "rgba(127, 127, 127, 0.08)";

      const composeMeta = ownerDoc.createElement("div");
      composeMeta.style.display = "flex";
      composeMeta.style.gap = "8px";
      composeMeta.style.justifyContent = "flex-end";
      composeMeta.style.alignItems = "center";
      composeMeta.style.fontSize = "12px";
      composeMeta.style.opacity = "0.85";
      const bridgeStatus = ownerDoc.createElement("span");
      bridgeStatus.style.display = "inline-flex";
      bridgeStatus.style.alignItems = "center";
      bridgeStatus.style.gap = "5px";
      bridgeStatus.style.padding = "1px 7px";
      bridgeStatus.style.minHeight = "24px";
      bridgeStatus.style.borderRadius = "999px";
      bridgeStatus.style.border = "1px solid rgba(120, 120, 120, 0.24)";
      bridgeStatus.style.background = "rgba(127, 127, 127, 0.08)";
      bridgeStatus.style.whiteSpace = "nowrap";
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
      contextBar.style.display = "none";
      contextBar.style.flexWrap = "wrap";
      contextBar.style.gap = "8px";
      contextBar.style.alignItems = "center";
      contextBar.style.padding = "0 2px";
      contextBar.style.marginRight = "auto";
      composeMeta.append(contextBar, bridgeStatus);

      const TEXT_CONTEXT_PREFIX = "[Text Context]\n";
      const TEXT_CONTEXT_SUFFIX = "\n[/Text Context]\n\n";
      let textContextValues: string[] = [];
      let contextTooltipEl: HTMLDivElement | null = null;

      const ensureContextTooltip = () => {
        if (contextTooltipEl) return contextTooltipEl;
        const el = ownerDoc.createElement("div");
        el.style.position = "fixed";
        el.style.zIndex = "99999";
        el.style.maxWidth = "460px";
        el.style.maxHeight = "220px";
        el.style.overflow = "auto";
        el.style.padding = "8px 10px";
        el.style.borderRadius = "8px";
        el.style.background = "rgba(34, 34, 34, 0.94)";
        el.style.color = "#fff";
        el.style.fontSize = "12px";
        el.style.lineHeight = "1.45";
        el.style.whiteSpace = "pre-wrap";
        el.style.wordBreak = "break-word";
        el.style.boxShadow = "0 6px 16px rgba(0, 0, 0, 0.35)";
        el.style.pointerEvents = "none";
        el.style.display = "none";
        const tooltipHost = ownerDoc.body || ownerDoc.documentElement;
        if (tooltipHost) {
          tooltipHost.appendChild(el);
        }
        contextTooltipEl = el;
        return el;
      };

      const showContextTooltip = (text: string, ev: MouseEvent) => {
        if (!text.trim()) return;
        const el = ensureContextTooltip();
        el.textContent = text;
        el.style.display = "block";
        const x = Math.min(ev.clientX + 12, (ownerDoc.defaultView?.innerWidth ?? 1200) - 480);
        const y = Math.min(ev.clientY + 12, (ownerDoc.defaultView?.innerHeight ?? 800) - 240);
        el.style.left = `${Math.max(8, x)}px`;
        el.style.top = `${Math.max(8, y)}px`;
      };

      const hideContextTooltip = () => {
        if (!contextTooltipEl) return;
        contextTooltipEl.style.display = "none";
      };

      const buildContextPayload = () =>
        textContextValues
          .map((v) => `${TEXT_CONTEXT_PREFIX}${v}${TEXT_CONTEXT_SUFFIX}`)
          .join("");

      const syncContextBar = () => {
        contextBar.replaceChildren();
        const has = textContextValues.length > 0;
        contextBar.style.display = has ? "flex" : "none";
        if (!has) return;
        textContextValues.forEach((_, idx) => {
          const chip = ownerDoc.createElement("div");
          chip.style.display = "inline-flex";
          chip.style.alignItems = "center";
          chip.style.gap = "5px";
          chip.style.padding = "1px 7px";
          chip.style.minHeight = "24px";
          chip.style.borderRadius = "999px";
          chip.style.background = "rgba(127, 127, 127, 0.08)";
          chip.style.border = "1px solid rgba(120, 120, 120, 0.24)";
          chip.style.whiteSpace = "nowrap";
          chip.title = textContextValues[idx] || "";
          const bindContextTooltip = (target: HTMLElement) => {
            target.addEventListener("mouseenter", (ev) => {
              showContextTooltip(textContextValues[idx] || "", ev as MouseEvent);
            });
            target.addEventListener("mousemove", (ev) => {
              showContextTooltip(textContextValues[idx] || "", ev as MouseEvent);
            });
            target.addEventListener("mouseleave", () => {
              hideContextTooltip();
            });
          };

          const chipIcon = ownerDoc.createElement("img");
          chipIcon.src = `chrome://${config.addonRef}/content/icons/copy.svg`;
          chipIcon.alt = "context";
          chipIcon.style.width = "13px";
          chipIcon.style.height = "13px";
          chipIcon.style.opacity = "0.75";

          const chipLabel = ownerDoc.createElement("span");
          chipLabel.textContent = `Text-${idx + 1}`;
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
          chipClose.addEventListener("click", () => {
            textContextValues = textContextValues.filter((_, i) => i !== idx);
            hideContextTooltip();
            syncContextBar();
            textArea.focus();
          });

          chip.append(chipIcon, chipLabel, chipClose);
          bindContextTooltip(chip);
          bindContextTooltip(chipIcon);
          bindContextTooltip(chipLabel);
          contextBar.appendChild(chip);
        });
      };

      const textArea = ownerDoc.createElement("textarea");
      textArea.placeholder = "询问关于这篇论文的问题…";
      textArea.style.minHeight = "90px";
      textArea.style.resize = "vertical";
      textArea.style.userSelect = "text";
      (textArea.style as any).MozUserSelect = "text";

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

      const slashBtn = createToolBtn("命令");
      const slashIcon = ownerDoc.createElement("img");
      slashIcon.src = `${iconBase}/bars.svg`;
      slashIcon.alt = "command";
      slashIcon.style.width = "16px";
      slashIcon.style.height = "16px";
      slashBtn.appendChild(slashIcon);

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
      const fontIcon = ownerDoc.createElement("img");
      fontIcon.src = `${iconBase}/font-size.svg`;
      fontIcon.alt = "font";
      fontIcon.style.width = "16px";
      fontIcon.style.height = "16px";
      fontBtn.appendChild(fontIcon);
      const applySelectedTextAsContext = () => {
        const selectedText = getSelectedTextFromZotero();
        if (!selectedText) {
          ztoolkit.log("[llm-ui] 未检测到可插入的划选文字");
          return;
        }
        // 仅在后台携带上下文，不污染用户输入框。
        textContextValues.push(selectedText);
        syncContextBar();
        textArea.focus();
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
      const imageIcon = ownerDoc.createElement("img");
      imageIcon.src = `${iconBase}/picture.svg`;
      imageIcon.alt = "image";
      imageIcon.style.width = "16px";
      imageIcon.style.height = "16px";
      screenshotBtn.appendChild(imageIcon);
      screenshotBtn.addEventListener("click", () => {
        ztoolkit.log("[llm-ui] 截图工具暂未接入 Zotero 原生截图 API");
      });

      let thinkingState: "Enable" | "Disable" = "Enable";
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
      thinkingStateWrap.addEventListener("mouseenter", () => {
        thinkingStateWrap.style.background = "rgba(127, 127, 127, 0.16)";
      });
      thinkingStateWrap.addEventListener("mouseleave", () => {
        thinkingStateWrap.style.background = "transparent";
      });
      const thinkingIcon = ownerDoc.createElement("img");
      thinkingIcon.src = `${iconBase}/think.svg`;
      thinkingIcon.alt = "thinking";
      thinkingIcon.style.width = "18px";
      thinkingIcon.style.height = "18px";
      const syncThinkingStateUI = () => {
        thinkingStateWrap.title =
          thinkingState === "Enable" ? "Thinking: 开启（点击切换）" : "Thinking: 关闭（点击切换）";
        thinkingStateWrap.style.opacity = thinkingState === "Enable" ? "1" : "0.45";
      };
      thinkingStateWrap.addEventListener("click", () => {
        thinkingState = thinkingState === "Enable" ? "Disable" : "Enable";
        syncThinkingStateUI();
      });
      syncThinkingStateUI();
      thinkingStateWrap.append(thinkingIcon);
      leftActions.append(slashBtn, fontBtn, screenshotBtn, thinkingStateWrap);

      const sendBtn = ownerDoc.createElement("button");
      sendBtn.textContent = "Send";
      sendBtn.disabled = false;
      (sendBtn as HTMLButtonElement).type = "button";
      sendBtn.style.borderRadius = "999px";
      sendBtn.style.padding = "10px 22px";
      sendBtn.style.border = "none";
      sendBtn.style.background = "linear-gradient(180deg, #3f83f8 0%, #2f6ee8 100%)";
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
      sendBtn.addEventListener("mouseenter", () => {
        if (sendBtn.disabled) return;
        sendBtn.style.background = "linear-gradient(180deg, #3274ed 0%, #265fdb 100%)";
      });
      sendBtn.addEventListener("mouseleave", () => {
        if (sendBtn.disabled) return;
        sendBtn.style.background = "linear-gradient(180deg, #3f83f8 0%, #2f6ee8 100%)";
      });

      composeActions.append(leftActions, sendBtn);
      composeCard.append(composeMeta, textArea, composeActions);

      function appendBubble(role: "user" | "system", text: string) {
        hideEmptyState();
        const row = ownerDoc.createElement("div");
        row.style.maxWidth = "90%";
        row.style.alignSelf = role === "user" ? "flex-end" : "flex-start";
        row.style.padding = "8px 10px";
        row.style.borderRadius = "10px";
        row.style.whiteSpace = "pre-wrap";
        row.style.wordBreak = "break-word";
        row.style.background =
          role === "user" ? "rgba(80, 140, 255, 0.18)" : "rgba(127, 127, 127, 0.12)";
        row.style.userSelect = "text";
        (row.style as any).MozUserSelect = "text";
        row.style.cursor = "text";
        row.textContent = text;
        conversationArea.appendChild(row);
        conversationArea.scrollTop = conversationArea.scrollHeight;
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

        const bubble = ownerDoc.createElement("div");
        bubble.style.maxWidth = "88%";
        bubble.style.alignSelf = "flex-end";
        bubble.style.padding = "10px 12px";
        bubble.style.borderRadius = "10px";
        bubble.style.whiteSpace = "pre-wrap";
        bubble.style.wordBreak = "break-word";
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
        conversationArea.scrollTop = conversationArea.scrollHeight;
        return { wrap, bubble };
      }

      function splitThinkingAndAnswer(full: string): { thinking: string; answer: string } {
        const text = (full || "").replace(/\r\n/g, "\n");
        // Prefer explicit tags if present.
        const tag = text.match(/<think>([\s\S]*?)<\/think>/i);
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

      function appendAssistantShell(modelLabelText: string) {
        hideEmptyState();
        // Inject keyframes once per document for typing dots.
        const typingStyleId = "ff-llm-typing-style";
        if (!ownerDoc.getElementById(typingStyleId)) {
          const style = ownerDoc.createElement("style");
          style.id = typingStyleId;
          style.textContent = `
@keyframes ffTypingDot {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
  40% { transform: translateY(-3px); opacity: 0.95; }
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

        const modelLabel = ownerDoc.createElement("div");
        modelLabel.style.fontSize = "12px";
        modelLabel.style.opacity = "0.55";
        modelLabel.style.fontWeight = "600";
        modelLabel.textContent = modelLabelText;

        const thinkingWrap = ownerDoc.createElement("div");
        thinkingWrap.style.borderRadius = "0";
        thinkingWrap.style.border = "none";
        thinkingWrap.style.background = "transparent";
        thinkingWrap.style.padding = "0";
        thinkingWrap.style.display = "none";

        const thinkingHead = ownerDoc.createElement("div");
        thinkingHead.style.display = "flex";
        thinkingHead.style.alignItems = "center";
        thinkingHead.style.gap = "6px";
        thinkingHead.style.cursor = "pointer";
        thinkingHead.style.userSelect = "none";
        (thinkingHead.style as any).MozUserSelect = "none";

        const arrow = ownerDoc.createElement("span");
        arrow.textContent = "▼";
        arrow.style.opacity = "0.65";

        const tTitle = ownerDoc.createElement("span");
        tTitle.textContent = "Thinking";
        tTitle.style.fontWeight = "600";

        thinkingHead.append(arrow, tTitle);

        const detailsLabel = ownerDoc.createElement("div");
        detailsLabel.textContent = "DETAILS";
        detailsLabel.style.fontSize = "10px";
        detailsLabel.style.opacity = "0.62";
        detailsLabel.style.letterSpacing = "0.12em";
        detailsLabel.style.marginTop = "6px";

        const thinkingBody = ownerDoc.createElement("div");
        thinkingBody.style.marginTop = "4px";
        thinkingBody.style.whiteSpace = "pre-wrap";
        thinkingBody.style.wordBreak = "break-word";
        thinkingBody.style.fontSize = "13px";
        thinkingBody.style.lineHeight = "1.7";
        thinkingBody.style.opacity = "0.78";
        thinkingBody.style.fontFamily =
          "'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Segoe UI', sans-serif";
        thinkingBody.textContent = " ";

        let open = true;
        const setOpen = (v: boolean) => {
          open = v;
          const bodyDisplay = open ? "block" : "none";
          detailsLabel.style.display = bodyDisplay;
          thinkingBody.style.display = bodyDisplay;
          arrow.textContent = open ? "▼" : "▶";
        };
        setOpen(true);
        thinkingHead.addEventListener("click", () => setOpen(!open));

        thinkingWrap.append(thinkingHead, detailsLabel, thinkingBody);

        const answerBubble = ownerDoc.createElement("div");
        answerBubble.style.padding = "10px 12px";
        answerBubble.style.borderRadius = "10px";
        answerBubble.style.whiteSpace = "pre-wrap";
        answerBubble.style.wordBreak = "break-word";
        answerBubble.style.background = "rgba(127, 127, 127, 0.10)";
        answerBubble.style.fontSize = "15px";
        answerBubble.style.lineHeight = "1.72";
        answerBubble.style.fontFamily =
          "'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Segoe UI', sans-serif";
        answerBubble.style.userSelect = "text";
        (answerBubble.style as any).MozUserSelect = "text";
        answerBubble.style.cursor = "text";
        const typingDots = ownerDoc.createElement("div");
        typingDots.style.display = "inline-flex";
        typingDots.style.alignItems = "center";
        typingDots.style.gap = "6px";
        typingDots.style.height = "18px";
        typingDots.style.padding = "2px 0";
        typingDots.style.opacity = "0.9";
        for (let i = 0; i < 3; i++) {
          const dot = ownerDoc.createElement("span");
          dot.style.width = "6px";
          dot.style.height = "6px";
          dot.style.borderRadius = "999px";
          dot.style.background = "rgba(95, 95, 95, 0.85)";
          dot.style.display = "inline-block";
          dot.style.animation = `ffTypingDot 1.05s ${i * 0.18}s infinite ease-in-out`;
          typingDots.appendChild(dot);
        }
        answerBubble.appendChild(typingDots);

        wrap.append(modelLabel, thinkingWrap, answerBubble);
        conversationArea.appendChild(wrap);
        conversationArea.scrollTop = conversationArea.scrollHeight;

        return {
          wrap,
          answerBubble,
          thinkingWrap,
          thinkingBody,
          setThinking: (t: string) => {
            const val = (t || "").trim();
            if (!val) {
              thinkingWrap.style.display = "none";
              thinkingBody.textContent = " ";
              return;
            }
            thinkingWrap.style.display = "block";
            thinkingBody.textContent = formatThinkingParagraphs(val);
          },
        };
      }

      function normalizeDisplayText(text: string) {
        return String(text || "").replace(/^\[zotero_current_item_id=\d+\]\n?/, "").trim();
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
          const assistant = appendAssistantShell("qwen3-vl-235b-a22b-thinking");
          const parsed = splitThinkingAndAnswer(record.content || "");
          const mergedThinking = [record.reasoning_content || "", parsed.thinking]
            .filter((s) => !!s && s.trim())
            .join("\n\n")
            .trim();
          assistant.setThinking(mergedThinking);
          assistant.answerBubble.textContent = parsed.answer || normalizeDisplayText(record.content) || " ";
        }
      }

      async function restoreChatHistories() {
        try {
          await ensureNanobotBridgeStarted();
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
        const message = textArea.value.trim();
        if (!message) {
          return;
        }
        const contextPayload = buildContextPayload();
        const messageWithContext = contextPayload ? `${contextPayload}${message}` : message;
        textArea.value = "";
        appendUserMessage(message);
        getTabHistory(activeTabId).push({ role: "user", content: message });
        sendBtn.disabled = true;
        sendBtn.style.opacity = "0.7";
        sendBtn.style.cursor = "not-allowed";
        ztoolkit.log("[llm-ui] sending:", messageWithContext);
        try {
          await ensureNanobotBridgeStarted();
          const streamSessionID = getActiveSessionID();
          const assistant = appendAssistantShell("qwen3-vl-235b-a22b-thinking");
          let streamedText = "";
          let streamedThinking = "";
          await streamFromNanobot(
            messageWithContext,
            streamSessionID,
            thinkingState,
            (delta) => {
              streamedText += delta;
              const parsedLive = splitThinkingAndAnswer(streamedText);
              if (thinkingState === "Enable") {
                assistant.setThinking(parsedLive.thinking);
              }
              assistant.answerBubble.textContent = parsedLive.answer || " ";
              conversationArea.scrollTop = conversationArea.scrollHeight;
            },
            (finalContent) => {
              if (finalContent) {
                if (!streamedText.trim()) {
                  streamedText = finalContent;
                } else if (thinkingState === "Enable") {
                  const finalParsed = splitThinkingAndAnswer(finalContent);
                  if (finalParsed.thinking) {
                    const liveParsed = splitThinkingAndAnswer(streamedText);
                    const mergedThinking = [liveParsed.thinking, finalParsed.thinking]
                      .filter((s) => !!s && s.trim())
                      .join("\n\n")
                      .trim();
                    assistant.setThinking(mergedThinking);
                  }
                }
                assistant.answerBubble.textContent = splitThinkingAndAnswer(streamedText).answer || " ";
                conversationArea.scrollTop = conversationArea.scrollHeight;
              }
            },
            (thinkingDelta) => {
              if (thinkingState !== "Enable") {
                return;
              }
              streamedThinking += thinkingDelta;
              assistant.setThinking(streamedThinking);
              conversationArea.scrollTop = conversationArea.scrollHeight;
            },
          );
          const parsed = splitThinkingAndAnswer(streamedText);
          if (thinkingState === "Enable") {
            const mergedThinking = [streamedThinking, parsed.thinking]
              .filter((s) => !!s && s.trim())
              .join("\n\n")
              .trim();
            assistant.setThinking(mergedThinking);
          } else {
            assistant.setThinking("");
          }
          if (parsed.answer) {
            assistant.answerBubble.textContent = parsed.answer;
          }
          if (!streamedText.trim()) {
            assistant.answerBubble.textContent = "(无输出)";
          }
          const finalParsed = splitThinkingAndAnswer(streamedText);
          getTabHistory(activeTabId).push({
            role: "assistant",
            content: finalParsed.answer || assistant.answerBubble.textContent || "",
            reasoning_content:
              thinkingState === "Enable"
                ? [streamedThinking, finalParsed.thinking].filter((s) => !!s && s.trim()).join("\n\n")
                : "",
          });
        } catch (e) {
          appendBubble("system", `发送失败: ${String(e)}`);
          ztoolkit.log("[llm-ui] send failed:", String(e));
        } finally {
          sendBtn.disabled = false;
          sendBtn.style.opacity = "1";
          sendBtn.style.cursor = "pointer";
          sendBtn.style.background = "linear-gradient(180deg, #3f83f8 0%, #2f6ee8 100%)";
          await refreshBridgeStatus();
        }
      }

      // 防止 item pane 上层事件抢占点击，确保按钮动作能触发。
      sendBtn.addEventListener("mousedown", (ev) => {
        ev.stopPropagation();
      });
      sendBtn.addEventListener("click", () => {
        void sendCurrentMessage();
      });
      sendBtn.onclick = () => {
        void sendCurrentMessage();
      };
      textArea.addEventListener("keydown", (ev: KeyboardEvent) => {
        if (ev.key === "Enter" && !ev.shiftKey) {
          ev.preventDefault();
          void sendCurrentMessage();
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

