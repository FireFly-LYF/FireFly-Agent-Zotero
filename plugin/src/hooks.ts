import {
  BasicExampleFactory,
  HelperExampleFactory,
  KeyExampleFactory,
  PromptExampleFactory,
  UIExampleFactory,
} from "./modules/examples";
import { getString, initLocale } from "./utils/locale";
import { registerPrefsScripts } from "./modules/preferenceScript";
import { createZToolkit } from "./utils/ztoolkit";
import { registerLLMItemPaneSection } from "./modules/itemPaneLLMUI";
import { isBridgeHealthy } from "./modules/nanobotBridge";
import { syncAllLibraryPDFsToWikiRawDir } from "./modules/wikiPdfSync";

/**
 * hooks.ts 的职责：
 * - 只做“生命周期/事件分发”（startup/shutdown/window load/notify/prefs/shortcuts/dialog events）
 * - 具体业务逻辑放在 modules/* 中（这里主要是模板 examples）
 */

async function onStartup() {
  // 等待 Zotero 完成初始化、解锁、UI 就绪后再继续执行。
  // Promise.all 表示“并发等待多个 Promise”，任意一个失败都会抛出异常并中断启动流程。
  await Promise.all([
    Zotero.initializationPromise,
    Zotero.unlockPromise,
    Zotero.uiReadyPromise,
  ]);

  // 初始化本地化（加载 FTL、准备 getString 等）
  initLocale();

  // ===== 以下均为“模板示例”注册逻辑 =====
  // 1) registerPrefs: 
  // 注册插件偏好设置页（Preferences 面板入口）。
  BasicExampleFactory.registerPrefs();

  // 2) registerNotifier: 
  // 注册 Zotero 事件观察者（tab/item/file），用于接收通知并转发到 hooks.onNotify。
  BasicExampleFactory.registerNotifier();

  // 3) registerShortcuts:
  // 模板示例快捷键会在启动时弹出 "Example Shortcuts" 提示，这里禁用。
  // KeyExampleFactory.registerShortcuts();

  // 4) registerExtraColumn: 
  // 在条目列表(ItemTree)注册一个文本扩展列。
  await UIExampleFactory.registerExtraColumn();

  // 5) registerExtraColumnWithCustomCell: 
  // 注册带自定义渲染单元格的扩展列。
  await UIExampleFactory.registerExtraColumnWithCustomCell();

  // 6) registerItemPaneCustomInfoRow: 
  // 在右侧条目信息区插入一行可编辑字段示例。
  //UIExampleFactory.registerItemPaneCustomInfoRow();

  // 7) registerLLMItemPaneSection:
  // 注册独立的 LLM Item Pane 页面模块（当前仅实现 UI，不包含具体对话功能）。
  // 清理模板示例页，避免出现与 LLM 无关的“未知插件页”。
  try {
    Zotero.ItemPaneManager.unregisterSection("example");
  } catch (_e) {}
  try {
    Zotero.ItemPaneManager.unregisterSection("reader-example");
  } catch (_e) {}
  registerLLMItemPaneSection();
  const bridgeReady = await isBridgeHealthy();
  ztoolkit.log("[nanobotBridge] startup check:", bridgeReady ? "ready" : "offline");
  void syncAllLibraryPDFsToWikiRawDir().catch((e) => {
    Zotero.logError(new Error(`[wiki-pdf-sync] startup sync failed: ${String(e)}`));
  });

  // 8) 关闭模板示例区块注册，仅保留 LLM 页面。

  // 对当前所有已打开的主窗口并发执行 onMainWindowLoad，并等待全部完成。
  // 语法说明：
  // - Zotero.getMainWindows() 返回窗口数组 win[]
  // - map((win) => onMainWindowLoad(win)) 把每个 win 映射为 Promise<void>
  // - Promise.all([...]) 并发等待这些 Promise 全部完成
  await Promise.all(Zotero.getMainWindows().map((win) => onMainWindowLoad(win)));

  // Mark initialized as true to confirm plugin loading status
  // outside of the plugin (e.g. scaffold testing process)
  addon.data.initialized = true;
}

async function onMainWindowLoad(win: _ZoteroTypes.MainWindow): Promise<void> {
  // 每个窗口都创建/绑定一份 ztoolkit（并提供 unregisterAll 统一清理入口）
  addon.data.ztoolkit = createZToolkit();

  // 将本插件的 mainWindow.ftl 注入到该窗口，使 l10nID 可用
  win.MozXULElement.insertFTLIfNeeded(
    `${addon.data.config.addonRef}-mainWindow.ftl`,
  );

  // ===== 以下均为“模板示例”注册逻辑（样式/菜单/Prompt 等）=====
  // 1) registerStyleSheet: 
  // 向主窗口注入插件样式文件（zoteroPane.css），并给 item pane 加示例 class。
  UIExampleFactory.registerStyleSheet(win);

  // 2) registerRightClickMenuItem: 
  // 在条目右键菜单注册一个菜单项，点击后触发 dialogExample。
  UIExampleFactory.registerRightClickMenuItem();

  // 3) registerRightClickMenuPopup: 
  // 在上面的菜单项附近再插入一个带子菜单的 menupopup。
  UIExampleFactory.registerRightClickMenuPopup(win);

  // 4) registerWindowMenuWithSeparator: 
  // 在 Zotero 顶部 File 菜单插入分隔线和一个示例菜单项。
  UIExampleFactory.registerWindowMenuWithSeparator();

  // 5) registerNormalCommandExample: 
  // 注册一个始终可见的 Prompt 命令（Shift+P 可唤起）。
  PromptExampleFactory.registerNormalCommandExample();

  // 6) registerAnonymousCommandExample: 
  // 注册一个匿名/搜索型 Prompt 命令，可搜索条目并跳转选中。
  PromptExampleFactory.registerAnonymousCommandExample(win);

  // 7) registerConditionalCommandExample: 注册一个带 when 条件的 Prompt 命令，仅在有选中条目时显示。
  PromptExampleFactory.registerConditionalCommandExample();

  // 主窗口完成后再次触发一次同步，避免启动早期环境未就绪导致首次同步被跳过。
  void syncAllLibraryPDFsToWikiRawDir().catch((e) => {
    Zotero.logError(new Error(`[wiki-pdf-sync] onMainWindowLoad sync failed: ${String(e)}`));
  });

  // 模板示例：每次启动弹出 “Helper Examples” 对话框。
  // 你若不想每次启动都弹，保持注释/删除即可。
  // addon.hooks.onDialogEvents("dialogExample");
}

async function onMainWindowUnload(win: Window): Promise<void> {
  // 清理所有由 ztoolkit 注册的 UI/监听等资源
  ztoolkit.unregisterAll();
  // 关闭可能残留的示例对话框
  addon.data.dialog?.window?.close();
}

function onShutdown(): void {
  // 插件禁用/卸载时的统一清理
  ztoolkit.unregisterAll();
  addon.data.dialog?.window?.close();
  // Remove addon object
  addon.data.alive = false;
  // @ts-expect-error - Plugin instance is not typed
  delete Zotero[addon.data.config.addonInstance];
}

/**
 * This function is just an example of dispatcher for Notify events.
 * Any operations should be placed in a function to keep this funcion clear.
 */
async function onNotify(
  event: string,
  type: string,
  ids: Array<string | number>,
  extraData: { [key: string]: any },
) {
  // Notify 事件分发示例：这里仅做日志 + 一个“选中 reader tab”时的示例回调
  ztoolkit.log("notify", event, type, ids, extraData);
  if (
    event == "select" &&
    type == "tab" &&
    extraData[ids[0]].type == "reader"
  ) {
    BasicExampleFactory.exampleNotifierCallback();
    ztoolkit.log("[wiki-pdf-convert] auto conversion disabled");
  } else {
    return;
  }
}

/**
 * This function is just an example of dispatcher for Preference UI events.
 * Any operations should be placed in a function to keep this funcion clear.
 * @param type event type
 * @param data event data
 */
async function onPrefsEvent(type: string, data: { [key: string]: any }) {
  switch (type) {
    case "load":
      // 偏好页面加载时注册脚本（处理设置页里的按钮/表格等交互）
      registerPrefsScripts(data.window);
      break;
    default:
      return;
  }
}

function onShortcuts(type: string) {
  // 快捷键示例分发
  switch (type) {
    case "larger":
      KeyExampleFactory.exampleShortcutLargerCallback();
      break;
    case "smaller":
      KeyExampleFactory.exampleShortcutSmallerCallback();
      break;
    default:
      break;
  }
}

function onDialogEvents(type: string) {
  // 对话框/工具示例分发（Helper Examples、剪贴板、文件选择、进度窗、虚拟表格等）
  switch (type) {
    case "dialogExample":
      HelperExampleFactory.dialogExample();
      break;
    case "clipboardExample":
      HelperExampleFactory.clipboardExample();
      break;
    case "filePickerExample":
      HelperExampleFactory.filePickerExample();
      break;
    case "progressWindowExample":
      HelperExampleFactory.progressWindowExample();
      break;
    case "vtableExample":
      HelperExampleFactory.vtableExample();
      break;
    default:
      break;
  }
}

// Add your hooks here. For element click, etc.
// Keep in mind hooks only do dispatch. Don't add code that does real jobs in hooks.
// Otherwise the code would be hard to read and maintain.

export default {
  onStartup,
  onShutdown,
  onMainWindowLoad,
  onMainWindowUnload,
  onNotify,
  onPrefsEvent,
  onShortcuts,
  onDialogEvents,
};
