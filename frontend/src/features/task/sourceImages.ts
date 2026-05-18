/**
 * 任务源图与子文档的匹配逻辑。
 */

import { imageNameToPageKey } from "./sourceImagePreview";

export function extractPageNamesFromMarkdown(markdown: string): readonly string[] {
  const seen = new Set<string>();
  const pages: string[] = [];
  for (const match of markdown.matchAll(/<!--\s*page:\s*(.+?)\s*-->/g)) {
    const page = match[1]?.trim();
    if (page === undefined || page === "" || seen.has(page)) continue;
    seen.add(page);
    pages.push(page);
  }
  return pages;
}

function basename(path: string): string {
  return imageNameToPageKey(path);
}

function getCandidateImagePrefixes(docDir: string | undefined): readonly string[] {
  if (docDir === undefined || docDir === "") return [""];
  const parts = docDir.split("/").filter((part) => part !== "");
  const prefixes: string[] = [];
  for (let i = parts.length; i > 0; i -= 1) {
    prefixes.push(`${parts.slice(0, i).join("/")}/`);
  }
  prefixes.push("");
  return prefixes;
}

export function filterImagesForDoc(
  allImages: readonly string[],
  docDir: string | undefined,
  markdown = "",
): readonly string[] {
  const rawPageNames = extractPageNamesFromMarkdown(markdown);
  const pageNames = new Set(rawPageNames);
  const pageKeys = new Set(
    rawPageNames.map((pageName) => imageNameToPageKey(pageName)),
  );
  const prefixes = getCandidateImagePrefixes(docDir);

  if (rawPageNames.length > 0) {
    for (const prefix of prefixes) {
      const matches = allImages.filter(
        (img) =>
          img.startsWith(prefix) &&
          (pageNames.has(img) || pageKeys.has(basename(img))),
      );
      if (matches.length > 0) return matches;
    }
  }

  if (docDir !== undefined && docDir !== "") {
    const directPrefix = `${docDir}/`;
    const byDocDir = allImages.filter((img) => img.startsWith(directPrefix));
    if (byDocDir.length > 0) return byDocDir;
  }

  return allImages;
}
