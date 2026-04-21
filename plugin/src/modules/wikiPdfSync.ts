const LLM_WIKI_PDF_ROOT_CANDIDATES = [
  "D:/A_Software/Agent/zotero/FireFly-Agent-Zotero/backend/llm-wiki/raw/pdf",
  "d:/A_Software/Agent/zotero/FireFly-Agent-Zotero/backend/llm-wiki/raw/pdf",
  "D:\\A_Software\\Agent\\zotero\\FireFly-Agent-Zotero\\backend\\llm-wiki\\raw\\pdf",
  "d:\\A_Software\\Agent\\zotero\\FireFly-Agent-Zotero\\backend\\llm-wiki\\raw\\pdf",
];

function logInfo(...args: any[]) {
  try {
    if (typeof ztoolkit !== "undefined" && ztoolkit?.log) {
      ztoolkit.log(...args);
      return;
    }
  } catch {
    // ignore
  }
  try {
    (globalThis as any).Zotero?.debug?.(args.map((v) => String(v)).join(" "));
  } catch {
    // ignore
  }
}

function sanitizeSegment(value: string): string {
  const clean = String(value || "")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .replace(/\s+/g, " ")
    .trim();
  return clean || "未分类";
}

function sanitizeFilename(value: string): string {
  return sanitizeSegment(value).replace(/\.+$/g, "");
}

function getPathUtils() {
  return (globalThis as any).PathUtils as
    | {
        join: (...parts: string[]) => string;
        filename?: (path: string) => string;
      }
    | undefined;
}

function getIOUtils() {
  return (globalThis as any).IOUtils as
    | {
        copy: (source: string, dest: string) => Promise<void>;
        makeDirectory: (
          path: string,
          options?: { ignoreExisting?: boolean; createAncestors?: boolean },
        ) => Promise<void>;
        stat: (path: string) => Promise<{ size?: number; lastModified?: number; type?: string }>;
        getChildren?: (path: string) => Promise<string[]>;
        remove?: (path: string, options?: { ignoreAbsent?: boolean }) => Promise<void>;
      }
    | undefined;
}

async function pathExists(path: string): Promise<boolean> {
  const ioUtils = getIOUtils();
  if (!ioUtils?.stat) return false;
  try {
    await ioUtils.stat(path);
    return true;
  } catch {
    return false;
  }
}

function normalizeWindowsPath(path: string): string {
  if (/^[a-zA-Z]:[\\/]/.test(path)) {
    return path.replace(/\//g, "\\");
  }
  return path;
}

async function resolveWikiPdfRoot(): Promise<string | null> {
  for (const candidate of LLM_WIKI_PDF_ROOT_CANDIDATES) {
    const normalized = normalizeWindowsPath(candidate);
    if (await pathExists(normalized)) {
      return normalized;
    }
  }
  return null;
}

async function ensureDir(path: string): Promise<void> {
  const ioUtils = getIOUtils();
  if (!ioUtils?.makeDirectory) {
    throw new Error("IOUtils.makeDirectory unavailable");
  }
  await ioUtils.makeDirectory(path, { ignoreExisting: true, createAncestors: true });
}

function normalizeForCompare(path: string): string {
  return String(path || "").replace(/\//g, "\\").toLowerCase();
}

function getRelativePath(fullPath: string, rootPath: string): string | null {
  const full = normalizeForCompare(fullPath);
  const root = normalizeForCompare(rootPath).replace(/[\\]+$/, "");
  if (!full.startsWith(root + "\\")) return null;
  return full.slice(root.length + 1);
}

function isManagedPdfFilename(path: string): boolean {
  const name = String(path || "").split(/[\\/]/).pop() || "";
  return /^[A-Z0-9]{8}_.+\.pdf$/i.test(name);
}

async function listPdfFilesRecursively(rootDir: string): Promise<string[]> {
  const ioUtils = getIOUtils();
  if (!ioUtils?.stat) return [];
  const results: string[] = [];

  const walkWithIOUtils = async (dir: string): Promise<void> => {
    const getChildren = (ioUtils as any).getChildren as ((path: string) => Promise<string[]>) | undefined;
    if (!getChildren) throw new Error("IOUtils.getChildren unavailable");
    const children = await getChildren(dir);
    for (const child of children) {
      try {
        const st = await ioUtils.stat(child);
        if (st?.type === "directory") {
          await walkWithIOUtils(child);
          continue;
        }
        if (/\.pdf$/i.test(child)) {
          results.push(child);
        }
      } catch {
        // ignore unreadable child
      }
    }
  };

  const walkWithOSFile = async (dir: string): Promise<void> => {
    const OSFile = (globalThis as any).OS?.File;
    if (!OSFile?.DirectoryIterator) return;
    const iterator = new OSFile.DirectoryIterator(dir);
    try {
      await iterator.forEach(async (entry: any) => {
        if (entry?.isDir) {
          await walkWithOSFile(String(entry.path));
          return;
        }
        if (entry?.isSymLink && !entry?.isDir) {
          const p = String(entry.path || "");
          if (/\.pdf$/i.test(p)) results.push(p);
          return;
        }
        const p = String(entry?.path || "");
        if (/\.pdf$/i.test(p)) {
          results.push(p);
        }
      });
    } finally {
      try {
        iterator.close();
      } catch {
        // ignore close failures
      }
    }
  };

  try {
    await walkWithIOUtils(rootDir);
  } catch {
    await walkWithOSFile(rootDir);
  }
  return results;
}

async function cleanupStaleManagedPdfs(
  wikiPdfRoot: string,
  expectedRelativeDestPaths: Set<string>,
): Promise<number> {
  const ioUtils = getIOUtils();
  if (!ioUtils?.remove) return 0;
  const existingPdfPaths = await listPdfFilesRecursively(wikiPdfRoot);
  let removed = 0;
  for (const fullPath of existingPdfPaths) {
    const rel = getRelativePath(fullPath, wikiPdfRoot);
    if (!rel) continue;
    if (!isManagedPdfFilename(fullPath)) continue;
    if (expectedRelativeDestPaths.has(rel)) continue;
    try {
      await ioUtils.remove(fullPath, { ignoreAbsent: true });
      removed += 1;
    } catch (e) {
      logInfo("[wiki-pdf-sync] remove failed:", String(e));
    }
  }
  return removed;
}

function getCollectionPathSegments(collectionID: number): string[] {
  const segments: string[] = [];
  let currentID: number | false | undefined = collectionID;
  while (currentID) {
    const col: any = Zotero.Collections.get(currentID);
    if (!col) break;
    segments.unshift(sanitizeSegment(String(col.name || "")));
    currentID = col.parentID;
  }
  return segments;
}

function buildDestinationFilename(attachment: any, sourcePath: string): string {
  const pathUtils = getPathUtils();
  const extName = pathUtils?.filename?.(sourcePath) || String(attachment?.attachmentFilename || "paper.pdf");
  const fallbackName = sanitizeFilename(extName || "paper.pdf");
  const key = sanitizeFilename(String(attachment?.key || ""));
  return key ? `${key}_${fallbackName}` : fallbackName;
}

export async function syncAllLibraryPDFsToWikiRawDir(): Promise<void> {
  const pathUtils = getPathUtils();
  const ioUtils = getIOUtils();
  if (!pathUtils?.join || !ioUtils?.copy) {
    logInfo("[wiki-pdf-sync] PathUtils/IOUtils unavailable, skip");
    return;
  }
  const wikiPdfRoot = await resolveWikiPdfRoot();
  if (!wikiPdfRoot) {
    logInfo(
      `[wiki-pdf-sync] target not found, skip: ${LLM_WIKI_PDF_ROOT_CANDIDATES.join(" | ")}`,
    );
    return;
  }

  const libraries = Zotero.Libraries.getAll().filter((lib: any) => !!lib && !lib.isFeeds);
  let copied = 0;
  let skipped = 0;
  let failed = 0;
  let removed = 0;
  let withCollection = 0;
  let withoutCollection = 0;
  const expectedRelativeDestPaths = new Set<string>();

  for (const lib of libraries) {
    const search = new Zotero.Search();
    (search as any).libraryID = lib.libraryID;
    search.addCondition("itemType", "is", "attachment");
    const ids = await search.search();
    for (const id of ids) {
      const attachment = Zotero.Items.get(id) as any;
      if (!attachment || !attachment.isAttachment?.()) continue;
      const contentType = String(attachment.attachmentContentType || "").toLowerCase();
      if (!contentType.includes("pdf")) continue;
      try {
        const sourcePath = String((await attachment.getFilePathAsync?.()) || "").trim();
        if (!sourcePath) {
          skipped += 1;
          continue;
        }

        const parentItemID = Number((attachment?.parentItemID as number) || 0);
        const parentItem = parentItemID > 0 ? (Zotero.Items.get(parentItemID) as any) : null;
        const ownerItem = parentItem ?? attachment;
        const collectionIDs = (ownerItem?.getCollections?.() as number[]) || [];
        if (collectionIDs.length > 0) {
          withCollection += 1;
        } else {
          withoutCollection += 1;
        }
        const targets = collectionIDs.length ? collectionIDs : [0];
        const destName = buildDestinationFilename(attachment, sourcePath);

        for (const collectionID of targets) {
          const segments = collectionID === 0 ? ["未分类"] : getCollectionPathSegments(collectionID);
          const destDir = pathUtils.join(wikiPdfRoot, ...segments);
          await ensureDir(destDir);
          const destPath = pathUtils.join(destDir, destName);
          const relDestPath = getRelativePath(destPath, wikiPdfRoot);
          if (relDestPath) {
            expectedRelativeDestPaths.add(relDestPath);
          }
          if (await pathExists(destPath)) {
            skipped += 1;
            continue;
          }
          await ioUtils.copy(sourcePath, destPath);
          copied += 1;
        }
      } catch (e) {
        failed += 1;
        logInfo("[wiki-pdf-sync] copy failed:", String(e));
      }
    }
  }
  removed = await cleanupStaleManagedPdfs(wikiPdfRoot, expectedRelativeDestPaths);
  logInfo(
    `[wiki-pdf-sync] done copied=${copied} skipped=${skipped} removed=${removed} failed=${failed} withCollection=${withCollection} withoutCollection=${withoutCollection}`,
  );
}
