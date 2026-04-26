/**
 * ConfirmDialog 组件测试
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "../../src/components/ConfirmDialog";
import { LanguageProvider } from "../../src/i18n";

function renderDialog(
  overrides: Partial<{
    onConfirm: () => void;
    onCancel: () => void;
  }> = {},
): { onConfirm: ReturnType<typeof vi.fn>; onCancel: ReturnType<typeof vi.fn> } {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <LanguageProvider>
      <ConfirmDialog
        title="删除"
        message="确认删除？"
        onConfirm={overrides.onConfirm ?? onConfirm}
        onCancel={overrides.onCancel ?? onCancel}
      />
    </LanguageProvider>,
  );
  return { onConfirm, onCancel };
}

describe("ConfirmDialog", () => {
  it("渲染 title / message / 按钮", () => {
    renderDialog();
    expect(screen.getByText("删除")).toBeInTheDocument();
    expect(screen.getByText("确认删除？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认" })).toBeInTheDocument();
  });

  it("点击确认按钮触发 onConfirm", async () => {
    const { onConfirm, onCancel } = renderDialog();
    await userEvent.click(screen.getByRole("button", { name: "确认" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("点击取消按钮触发 onCancel", async () => {
    const { onCancel } = renderDialog();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("点击 overlay 区域（外层）触发 onCancel", async () => {
    const { onCancel } = renderDialog();
    /* overlay 是 role=button + 包裹 dialog 的最外层 */
    const overlays = screen.getAllByRole("button");
    /* overlay 不是带具体 name 的按钮，是最外层 div role=button */
    const overlay = overlays.find(
      (b) => !b.textContent || (b.textContent !== "确认" && b.textContent !== "取消"),
    );
    expect(overlay).toBeDefined();
    if (overlay) await userEvent.click(overlay);
    expect(onCancel).toHaveBeenCalled();
  });

  it("Escape 键触发 onCancel", () => {
    const { onCancel } = renderDialog();
    const overlays = screen.getAllByRole("button");
    const overlay = overlays.find((b) =>
      b.classList.contains("confirm-overlay"),
    );
    expect(overlay).toBeDefined();
    if (overlay) {
      fireEvent.keyDown(overlay, { key: "Escape" });
    }
    expect(onCancel).toHaveBeenCalled();
  });

  it("点击 dialog 内容不冒泡到 overlay（不触发 onCancel）", async () => {
    const { onCancel } = renderDialog();
    await userEvent.click(screen.getByRole("dialog"));
    expect(onCancel).not.toHaveBeenCalled();
  });
});
