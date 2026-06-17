import { describe, expect, it } from "vitest";

import {
  ACCEPT_ATTR,
  ALLOWED_EXTENSIONS,
  classifySelection,
  fileKind,
  isAcceptedFilename,
  isPdfFilename,
} from "../../../src/features/task/fileKind";

describe("fileKind", () => {
  it("识别图片扩展名（大小写不敏感）", () => {
    expect(fileKind("a.jpg")).toBe("image");
    expect(fileKind("a.JPG")).toBe("image");
    expect(fileKind("a.jpeg")).toBe("image");
    expect(fileKind("a.PNG")).toBe("image");
    expect(fileKind("a.bmp")).toBe("image");
    expect(fileKind("a.tiff")).toBe("image");
    expect(fileKind("a.tif")).toBe("image");
  });

  it("识别 PDF 扩展名（大小写不敏感）", () => {
    expect(fileKind("doc.pdf")).toBe("pdf");
    expect(fileKind("doc.PDF")).toBe("pdf");
    expect(isPdfFilename("doc.PdF")).toBe(true);
    expect(isPdfFilename("doc.jpg")).toBe(false);
  });

  it("不支持的扩展名 / 无扩展名返回 undefined", () => {
    expect(fileKind("a.txt")).toBeUndefined();
    expect(fileKind("noext")).toBeUndefined();
    expect(isAcceptedFilename("a.txt")).toBe(false);
    expect(isAcceptedFilename("a.png")).toBe(true);
  });

  it("ALLOWED_EXTENSIONS 同时含图片与 PDF", () => {
    expect(ALLOWED_EXTENSIONS.has(".png")).toBe(true);
    expect(ALLOWED_EXTENSIONS.has(".pdf")).toBe(true);
    expect(ALLOWED_EXTENSIONS.has(".txt")).toBe(false);
  });

  it("ACCEPT_ATTR 含 PDF MIME", () => {
    expect(ACCEPT_ATTR).toContain("application/pdf");
    expect(ACCEPT_ATTR).toContain("image/jpeg");
  });
});

describe("classifySelection", () => {
  it("全图片 → image", () => {
    expect(classifySelection(["a.jpg", "b.png"])).toBe("image");
  });

  it("全 PDF → pdf", () => {
    expect(classifySelection(["a.pdf", "b.PDF"])).toBe("pdf");
  });

  it("图片 + PDF 混合 → mixed", () => {
    expect(classifySelection(["a.jpg", "b.pdf"])).toBe("mixed");
  });

  it("无受支持文件 → empty（忽略不支持扩展名）", () => {
    expect(classifySelection([])).toBe("empty");
    expect(classifySelection(["a.txt", "readme"])).toBe("empty");
  });

  it("受支持文件混杂不支持文件时，只按受支持文件判定", () => {
    expect(classifySelection(["a.jpg", "note.txt"])).toBe("image");
    expect(classifySelection(["a.pdf", "note.txt"])).toBe("pdf");
  });
});
