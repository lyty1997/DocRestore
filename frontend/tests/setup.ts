/**
 * Vitest 全局 setup：注入 jest-dom 断言、清理 DOM、重置 localStorage。
 *
 * 所有测试运行前都会执行；个别用例如需更细粒度 mock 应在用例内 vi.spyOn / vi.fn。
 */

import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.removeAttribute("lang");
});
