import { getLocaleID } from "../utils/locale";

/**
 * 独立的 Item Pane LLM 页面模块（仅 UI 结构，不包含任何模型调用逻辑）。
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
      const bodyEl = body as HTMLElement;
      bodyEl.style.height = "calc(100vh - 160px)";
      bodyEl.style.minHeight = "680px";
      bodyEl.style.display = "flex";
      bodyEl.style.flexDirection = "column";

      const root = doc.createElement("div");
      root.style.display = "flex";
      root.style.flexDirection = "column";
      root.style.gap = "10px";
      root.style.padding = "10px 0";
      root.style.height = "100%";

      const topBar = doc.createElement("div");
      topBar.style.display = "flex";
      topBar.style.justifyContent = "space-between";
      topBar.style.alignItems = "center";

      const topLeft = doc.createElement("div");
      topLeft.style.display = "flex";
      topLeft.style.alignItems = "center";
      topLeft.style.gap = "8px";
      const appName = doc.createElement("strong");
      appName.textContent = "FireFly-Agent-Zotero";
      const modeChip = doc.createElement("span");
      modeChip.textContent = "Paper chat";
      modeChip.style.padding = "2px 8px";
      modeChip.style.borderRadius = "999px";
      modeChip.style.background = "rgba(125, 125, 125, 0.18)";
      modeChip.style.fontSize = "12px";
      topLeft.append(appName, modeChip);

      const topRight = doc.createElement("div");
      topRight.style.display = "flex";
      topRight.style.gap = "10px";
      topRight.style.fontSize = "12px";
      topRight.style.opacity = "0.8";
      topRight.textContent = "⤢ ⚙ ↓ Clear";
      topBar.append(topLeft, topRight);

      const hero = doc.createElement("div");
      hero.style.textAlign = "center";
      hero.style.padding = "18px 8px";
      const heroTitle = doc.createElement("div");
      heroTitle.textContent = "LLM-for-Zotero";
      heroTitle.style.fontSize = "26px";
      heroTitle.style.fontWeight = "700";
      const heroSub = doc.createElement("div");
      heroSub.textContent = "从这里开始，读懂这篇论文的一切";
      heroSub.style.opacity = "0.6";
      heroSub.style.marginTop = "6px";
      hero.append(heroTitle, heroSub);

      const intro = doc.createElement("div");
      intro.style.fontSize = "13px";
      intro.style.opacity = "0.78";
      intro.style.lineHeight = "1.6";
      intro.textContent =
        "论文对话回答关于当前活跃论文的问题。示例界面仅用于展示布局，不包含真实模型请求。";

      const quickActions = doc.createElement("div");
      quickActions.style.display = "flex";
      quickActions.style.flexWrap = "wrap";
      quickActions.style.gap = "8px";
      for (const label of ["Summarize", "Key Points", "Methodology", "Limitations"]) {
        const b = doc.createElement("button");
        b.textContent = label;
        b.disabled = true;
        b.style.borderRadius = "999px";
        b.style.padding = "6px 10px";
        b.style.opacity = "0.7";
        quickActions.appendChild(b);
      }

      // 中间留空区域：后续用于渲染对话消息
      const conversationArea = doc.createElement("div");
      conversationArea.style.flex = "1";
      conversationArea.style.minHeight = "180px";
      conversationArea.style.border = "1px dashed var(--color-border)";
      conversationArea.style.borderRadius = "10px";
      conversationArea.style.opacity = "0.65";
      conversationArea.style.display = "flex";
      conversationArea.style.alignItems = "center";
      conversationArea.style.justifyContent = "center";
      conversationArea.style.fontSize = "13px";
      conversationArea.textContent = "对话区域（预留）";

      const composeCard = doc.createElement("div");
      composeCard.style.border = "1px solid var(--color-border)";
      composeCard.style.borderRadius = "12px";
      composeCard.style.padding = "10px";
      composeCard.style.display = "flex";
      composeCard.style.flexDirection = "column";
      composeCard.style.gap = "10px";
      composeCard.style.background = "rgba(127, 127, 127, 0.08)";

      const composeMeta = doc.createElement("div");
      composeMeta.style.display = "flex";
      composeMeta.style.gap = "8px";
      composeMeta.style.fontSize = "12px";
      composeMeta.style.opacity = "0.85";
      composeMeta.textContent = "◎ Agent(beta)   Paper-Text";

      const textArea = doc.createElement("textarea");
      textArea.placeholder = "询问关于这篇论文的问题…（仅 UI，发送未实现）";
      textArea.style.minHeight = "90px";
      textArea.style.resize = "vertical";

      const composeActions = doc.createElement("div");
      composeActions.style.display = "flex";
      composeActions.style.justifyContent = "space-between";
      composeActions.style.alignItems = "center";

      const leftActions = doc.createElement("div");
      leftActions.style.fontSize = "12px";
      leftActions.style.opacity = "0.85";
      leftActions.textContent = "/   添加文本   截图   qwen3-vl-235b-a22b-thinking";

      const sendBtn = doc.createElement("button");
      sendBtn.textContent = "Send";
      sendBtn.disabled = true;
      sendBtn.style.borderRadius = "999px";
      sendBtn.style.padding = "6px 16px";

      composeActions.append(leftActions, sendBtn);
      composeCard.append(composeMeta, textArea, composeActions);

      const footerMeta = doc.createElement("div");
      footerMeta.style.fontSize = "12px";
      footerMeta.style.opacity = "0.65";
      footerMeta.textContent = `当前条目: ${item?.getField("title") || "未选中"} | tabType: ${tabType}`;

      root.append(topBar, hero, intro, quickActions, conversationArea, composeCard, footerMeta);
      (body as any).replaceChildren?.(root);
    },
  });
}

