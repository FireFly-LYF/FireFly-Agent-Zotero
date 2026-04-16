import { getLocaleID } from "../utils/locale";
import { ensureNanobotBridgeStarted, isBridgeHealthy, streamFromNanobot } from "./nanobotBridge";

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
      icon: "chrome://zotero/skin/16/universal/book.svg",
    },
    sidenav: {
      l10nID: getLocaleID("item-section-example1-sidenav-tooltip"),
      icon: "chrome://zotero/skin/20/universal/save.svg",
    },
    onRender: ({ body, item, tabType }) => {
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
      topBar.style.justifyContent = "space-between";
      topBar.style.alignItems = "center";

      const topLeft = ownerDoc.createElement("div");
      topLeft.style.display = "flex";
      topLeft.style.alignItems = "center";
      topLeft.style.gap = "8px";
      const appName = ownerDoc.createElement("strong");
      appName.textContent = "FireFly-Agent-Zotero";
      const modeChip = ownerDoc.createElement("span");
      modeChip.textContent = "Paper chat";
      modeChip.style.padding = "2px 8px";
      modeChip.style.borderRadius = "999px";
      modeChip.style.background = "rgba(125, 125, 125, 0.18)";
      modeChip.style.fontSize = "12px";
      topLeft.append(appName, modeChip);

      const topRight = ownerDoc.createElement("div");
      topRight.style.display = "flex";
      topRight.style.gap = "10px";
      topRight.style.fontSize = "12px";
      topRight.style.opacity = "0.8";
      topRight.textContent = "⚙ Bridge";
      topBar.append(topLeft, topRight);

      const hero = ownerDoc.createElement("div");
      hero.style.textAlign = "center";
      hero.style.padding = "18px 8px";
      const heroTitle = ownerDoc.createElement("div");
      heroTitle.textContent = "LLM-for-Zotero";
      heroTitle.style.fontSize = "26px";
      heroTitle.style.fontWeight = "700";
      const heroSub = ownerDoc.createElement("div");
      heroSub.textContent = "从这里开始，读懂这篇论文的一切";
      heroSub.style.opacity = "0.6";
      heroSub.style.marginTop = "6px";
      hero.append(heroTitle, heroSub);

      const intro = ownerDoc.createElement("div");
      intro.style.fontSize = "13px";
      intro.style.opacity = "0.78";
      intro.style.lineHeight = "1.6";
      intro.textContent = "论文对话会把问题发送到本地 nanobot agent（zotero bridge）。";

      const quickActions = ownerDoc.createElement("div");
      quickActions.style.display = "flex";
      quickActions.style.flexWrap = "wrap";
      quickActions.style.gap = "8px";
      for (const label of ["Summarize", "Key Points", "Methodology", "Limitations"]) {
        const b = ownerDoc.createElement("button");
        b.textContent = label;
        b.style.borderRadius = "999px";
        b.style.padding = "6px 10px";
        b.style.opacity = "0.7";
        b.addEventListener("click", () => {
          textArea.value = `${label} this paper.`;
        });
        quickActions.appendChild(b);
      }

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
      composeMeta.style.justifyContent = "space-between";
      composeMeta.style.fontSize = "12px";
      composeMeta.style.opacity = "0.85";
      const composeMetaLeft = ownerDoc.createElement("span");
      composeMetaLeft.textContent = "◎ Agent(beta)   Paper-Text";
      const bridgeStatus = ownerDoc.createElement("span");
      bridgeStatus.textContent = "Bridge checking...";
      composeMeta.append(composeMetaLeft, bridgeStatus);

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

      const leftActions = ownerDoc.createElement("div");
      leftActions.style.fontSize = "12px";
      leftActions.style.opacity = "0.85";
      leftActions.textContent = "/   添加文本   截图   qwen3-vl-235b-a22b-thinking";

      const sendBtn = ownerDoc.createElement("button");
      sendBtn.textContent = "Send";
      sendBtn.disabled = false;
      (sendBtn as HTMLButtonElement).type = "button";
      sendBtn.style.borderRadius = "999px";
      sendBtn.style.padding = "6px 16px";

      composeActions.append(leftActions, sendBtn);
      composeCard.append(composeMeta, textArea, composeActions);

      const footerMeta = ownerDoc.createElement("div");
      footerMeta.style.fontSize = "12px";
      footerMeta.style.opacity = "0.65";
      footerMeta.textContent = `当前条目: ${item?.getField("title") || "未选中"} | tabType: ${tabType}`;

      const runtimeStatus = ownerDoc.createElement("div");
      runtimeStatus.style.fontSize = "12px";
      runtimeStatus.style.opacity = "0.75";
      runtimeStatus.textContent = "状态: 就绪";

      function appendBubble(role: "user" | "system", text: string) {
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

      async function refreshBridgeStatus() {
        const ok = await isBridgeHealthy();
        bridgeStatus.textContent = ok ? "Bridge online" : "Bridge offline";
      }

      async function sendCurrentMessage() {
        const message = textArea.value.trim();
        if (!message) {
          runtimeStatus.textContent = "状态: 请输入问题后再发送";
          return;
        }
        textArea.value = "";
        appendBubble("user", message);
        sendBtn.disabled = true;
        runtimeStatus.textContent = "状态: 正在发送...";
        ztoolkit.log("[llm-ui] sending:", message);
        try {
          await ensureNanobotBridgeStarted();
          const itemID = item?.id ? String(item.id) : "unknown-item";
          const streamSessionID = `zotero:item-${itemID}`;
          // 在消息正文里冗余携带当前条目 ID，避免 session 路由异常时丢失上下文。
          const messageWithContext =
            item?.id && Number.isFinite(item.id)
              ? `[zotero_current_item_id=${itemID}]\n${message}`
              : message;
          const assistantBubble = appendBubble("system", "");
          let streamedText = "";
          await streamFromNanobot(
            messageWithContext,
            streamSessionID,
            (delta) => {
              streamedText += delta;
              assistantBubble.textContent = streamedText || " ";
              conversationArea.scrollTop = conversationArea.scrollHeight;
            },
            (finalContent) => {
              if (!streamedText && finalContent) {
                streamedText = finalContent;
                assistantBubble.textContent = streamedText;
                conversationArea.scrollTop = conversationArea.scrollHeight;
              }
            },
          );
          if (!streamedText.trim()) {
            assistantBubble.textContent = "(无输出)";
          }
          runtimeStatus.textContent = "状态: 对话完成";
        } catch (e) {
          appendBubble("system", `发送失败: ${String(e)}`);
          runtimeStatus.textContent = `状态: 发送失败 (${String(e)})`;
          ztoolkit.log("[llm-ui] send failed:", String(e));
        } finally {
          sendBtn.disabled = false;
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

      root.append(topBar, hero, intro, quickActions, conversationArea, composeCard, footerMeta, runtimeStatus);
      (body as any).replaceChildren?.(root);
    },
  });
}

