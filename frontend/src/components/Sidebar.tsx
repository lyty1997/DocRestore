/**
 * 可拖拽调整宽度 + 可折叠的侧边栏组件
 *
 * 包含：品牌区、新建任务按钮、可折叠任务列表、页脚
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { Theme } from "../hooks/useTheme";
import {
  LANGUAGE_OPTIONS,
  useTranslation,
  type Language,
} from "../i18n";
import {
  SidebarTaskList,
  type SidebarTaskListHandle,
} from "./SidebarTaskList";

const SIDEBAR_MIN = 60;
const SIDEBAR_MAX = 400;
const SIDEBAR_DEFAULT = 220;
const COLLAPSE_THRESHOLD = 100;

interface SidebarProps {
  /** 当前选中的历史任务 ID（undefined 表示新建模式） */
  readonly selectedTaskId: string | undefined;
  /** 选中历史任务回调 */
  readonly onSelectTask: (taskId: string) => void;
  /** 点击"新建任务"回调 */
  readonly onNewTask: () => void;
  /**
   * 任务被删除时的回调。批量删除会对每个删除的 task_id 调用一次。
   * 父组件需据此清理 selectedTaskId（若被选中的任务正好被删）。
   */
  readonly onTaskDeleted: (taskId: string) => void;
  readonly onWidthChange: (width: number) => void;
  readonly onTokenSettings: () => void;
  readonly theme: Theme;
  readonly onToggleTheme: () => void;
  /** 暴露给父组件的任务列表刷新句柄 */
  readonly taskListRef: React.Ref<SidebarTaskListHandle>;
}

export function Sidebar({
  selectedTaskId,
  onSelectTask,
  onNewTask,
  onTaskDeleted,
  onWidthChange,
  onTokenSettings,
  theme,
  onToggleTheme,
  taskListRef,
}: SidebarProps): React.JSX.Element {
  const { t, language, setLanguage } = useTranslation();
  const [width, setWidth] = useState(SIDEBAR_DEFAULT);
  const [collapsed, setCollapsed] = useState(false);
  const [dragging, setDragging] = useState(false);

  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const effectiveWidth = collapsed ? SIDEBAR_MIN : width;

  /* 宽度变化时通知父组件 */
  useEffect(() => {
    onWidthChange(effectiveWidth);
  }, [effectiveWidth, onWidthChange]);

  /* ── 拖拽事件 ── */
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      isDragging.current = true;
      startX.current = e.clientX;
      startWidth.current = effectiveWidth;
      setDragging(true);
      e.preventDefault();
    },
    [effectiveWidth],
  );

  useEffect(() => {
    const onMouseMove = (e: MouseEvent): void => {
      if (!isDragging.current) return;
      const next = Math.min(
        SIDEBAR_MAX,
        Math.max(SIDEBAR_MIN, startWidth.current + e.clientX - startX.current),
      );
      if (next <= COLLAPSE_THRESHOLD) {
        setCollapsed(true);
      } else {
        setCollapsed(false);
        setWidth(next);
      }
    };

    const onMouseUp = (): void => {
      if (!isDragging.current) return;
      isDragging.current = false;
      setDragging(false);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  /* ── 折叠切换 ── */
  const toggleCollapse = useCallback(() => {
    setCollapsed((c) => !c);
  }, []);

  const sidebarClass = [
    "sidebar",
    collapsed ? "sidebar--collapsed" : "",
    dragging ? "is-dragging" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <aside
      className={sidebarClass}
      style={{ width: effectiveWidth }}
    >
      {/* 品牌区 */}
      <div className="sidebar-brand">
        {!collapsed && (
          <>
            <img
              src={theme === "dark" ? "/logo_dark.png" : "/logo_light.png"}
              alt="DocRestore"
              className="brand-logo"
            />
            <span className="brand-tag">AI Document Recovery</span>
          </>
        )}
      </div>

      {/* 新建任务按钮 */}
      <div className="sidebar-nav">
        <button
          type="button"
          className={`nav-item ${selectedTaskId === undefined ? "active" : ""}`}
          onClick={onNewTask}
          title={t("sidebar.newTask")}
        >
          <span className="nav-icon">+</span>
          {!collapsed && <span>{t("sidebar.newTask")}</span>}
        </button>
      </div>

      {/* 可折叠任务列表 */}
      <SidebarTaskList
        ref={taskListRef}
        selectedTaskId={selectedTaskId}
        onSelect={onSelectTask}
        onDeleted={onTaskDeleted}
        collapsed={collapsed}
      />

      {/* 折叠切换按钮 */}
      <button
        type="button"
        className="sidebar-toggle"
        onClick={toggleCollapse}
        aria-label={collapsed ? t("sidebar.expandSidebar") : t("sidebar.collapseSidebar")}
        title={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
      >
        {collapsed ? "\u203A" : "\u2039"}
      </button>

      {/* 页脚 */}
      {!collapsed && (
        <footer className="sidebar-footer">
          <button
            type="button"
            className="theme-toggle-btn"
            onClick={onToggleTheme}
            title={theme === "dark" ? t("sidebar.switchDayMode") : t("sidebar.switchNightMode")}
          >
            {theme === "dark" ? t("sidebar.dayMode") : t("sidebar.nightMode")}
          </button>
          <select
            className="language-select"
            value={language}
            onChange={(e) => { setLanguage(e.target.value as Language); }}
          >
            {LANGUAGE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="token-settings-btn"
            onClick={onTokenSettings}
            title={t("sidebar.apiTokenSettings")}
          >
            {t("sidebar.apiToken")}
          </button>
          <p>&copy; 2026 Vincent-lu (lyty1997)</p>
          <p>Apache License 2.0</p>
        </footer>
      )}

      {/* 拖拽手柄 */}
      <div
        className={`sidebar-resize-handle ${dragging ? "active" : ""}`}
        onMouseDown={handleMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-label={t("sidebar.resizeSidebar")}
      />
    </aside>
  );
}
