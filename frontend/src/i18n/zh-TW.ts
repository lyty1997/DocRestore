/**
 * 繁體中文翻譯
 *
 * 類型約束：必須實現 zhCN 的所有 key，缺少任何 key 會導致編譯錯誤。
 */

import type { TranslationKey } from "./zh-CN";

export const zhTW: Record<TranslationKey, string> = {
  // ── 通用 ──
  "common.cancel": "取消",
  "common.confirm": "確認",
  "common.delete": "刪除",
  "common.save": "儲存",
  "common.saving": "儲存中...",
  "common.saveFailed": "儲存失敗",
  "common.retry": "重試",
  "common.loading": "載入中...",
  "common.close": "關閉",
  "common.clear": "清除",
  "common.enabled": "已開啟",
  "common.disabled": "已關閉",
  "common.preview": "預覽",
  "common.edit": "編輯",
  "common.dateLocale": "zh-TW",

  // ── 狀態 ──
  "status.pending": "等待中",
  "status.processing": "處理中",
  "status.completed": "已完成",
  "status.failed": "已失敗",

  // ── 側邊欄 ──
  "sidebar.newTask": "新建任務",
  "sidebar.expandSidebar": "展開側邊欄",
  "sidebar.collapseSidebar": "折疊側邊欄",
  "sidebar.expand": "展開",
  "sidebar.collapse": "折疊",
  "sidebar.switchDayMode": "切換日間模式",
  "sidebar.switchNightMode": "切換夜間模式",
  "sidebar.dayMode": "☀ 日間模式",
  "sidebar.nightMode": "☾ 夜間模式",
  "sidebar.apiTokenSettings": "API Token 設定",
  "sidebar.apiToken": "API Token",
  "sidebar.resizeSidebar": "調整側邊欄寬度",

  // ── 任務列表 ──
  "taskList.title": "任務列表",
  "taskList.empty": "暫無任務",
  "taskList.loadMore": "載入更多",

  // ── 任務表單 ──
  "taskForm.sourceLabel": "圖片來源",
  "taskForm.outputDirLabel": "輸出目錄",
  "taskForm.outputDirPlaceholder": "留空則自動產生至 /tmp/docrestore_xxx",
  "taskForm.browse": "瀏覽...",
  "taskForm.ocrEngine": "OCR 引擎",
  "taskForm.gpu": "GPU",
  "taskForm.paddleOcrName": "PaddleOCR-VL（推薦）",
  "taskForm.paddleOcrDesc": "輕量快速，適合常規文件，CPU/GPU 均可運行",
  "taskForm.deepseekOcrName": "DeepSeek-OCR-2",
  "taskForm.deepseekOcrDesc": "高精度大模型，適合複雜版面，需要 GPU",
  "taskForm.gpu0": "GPU 0 (A2)",
  "taskForm.gpu1": "GPU 1 (RTX 4070 Super)",
  "taskForm.llmConfigExpanded": "▾ LLM 精修設定",
  "taskForm.llmConfigCollapsed": "▸ LLM 精修設定",
  "taskForm.modelName": "模型名稱",
  "taskForm.modelNamePlaceholder": "例如 openai/gpt-4o、openai/glm-5",
  "taskForm.apiBaseUrl": "API Base URL",
  "taskForm.apiBaseUrlPlaceholder": "留空使用預設位址",
  "taskForm.apiKey": "API Key",
  "taskForm.apiKeyPlaceholder": "留空使用伺服器預設金鑰",
  "taskForm.rememberConfig": "記住設定",
  "taskForm.llmHint":
    "不填則跳過 LLM 精修，僅輸出 OCR 原始結果。模型名稱使用 litellm 格式（provider/model）。",
  "taskForm.storageWarning": " 設定（含 API Key）將以明文儲存於瀏覽器本地。",
  "taskForm.piiTitle": "脫敏功能",
  "taskForm.piiDesc":
    "開啟後自動檢測並脫敏手機號碼、電子郵件、身分證號等隱私資訊。也可新增自定義敏感詞，無需開啟即生效。",
  "taskForm.piiWordPlaceholder": "輸入自定義敏感詞，按 Enter 新增",
  "taskForm.piiCodePlaceholder": "代號（可選，留空使用預設佔位符）",
  "taskForm.piiWordAdd": "新增",
  "taskForm.piiWordRemove": "移除 {word}",
  "taskForm.startProcessing": "開始處理",

  // ── 任務進度 ──
  "taskProgress.stageInit": "引擎初始化",
  "taskProgress.stageOcr": "OCR 辨識",
  "taskProgress.stageClean": "文字清洗",
  "taskProgress.stageMerge": "去重合併",
  "taskProgress.stageRefine": "LLM 精修",
  "taskProgress.stageRender": "渲染輸出",
  "taskProgress.waiting": "等待開始",
  "taskProgress.taskLabel": "任務：{taskId}",
  "taskProgress.polling": "輪詢",

  // ── 任務結果 ──
  "taskResult.title": "處理結果",
  "taskResult.downloadZip": "下載結果（zip）",
  "taskResult.docTab": "文件 {index}",
  "taskResult.processNew": "處理新文件",
  "taskResult.resetBtn": "重新開始",

  // ── 任務詳情 ──
  "taskDetail.title": "任務詳情",
  "taskDetail.idLabel": "ID: {taskId}",
  "taskDetail.cancelTask": "取消任務",
  "taskDetail.cancelConfirm": "確定要取消任務 {taskId} 嗎？",
  "taskDetail.deleteTask": "刪除任務",
  "taskDetail.deleteConfirm":
    "確定要刪除任務 {taskId} 及其所有產物嗎？此操作不可撤銷。",
  "taskDetail.downloadZip": "下載結果（zip）",
  "taskDetail.errorLabel": "錯誤：",
  "taskDetail.loadingResults": "載入結果...",
  "taskDetail.docPreview": "文件預覽",
  "taskDetail.noResults": "暫無可用結果",
  "taskDetail.loadError": "載入任務資訊失敗",
  "taskDetail.loadingTask": "載入任務資訊...",
  "taskDetail.cancelFailed": "取消失敗",
  "taskDetail.deleteFailed": "刪除失敗",
  "taskDetail.retryFailed": "重試失敗",

  // ── App 級別 ──
  "app.processingFailed": "處理失敗",
  "app.unknownError": "未知錯誤",

  // ── 回到頂部 ──
  "backToTop.label": "回到頂部",

  // ── Token 設定 ──
  "tokenSettings.title": "API Token",
  "tokenSettings.hintPrefix": "對應伺服端環境變數 ",
  "tokenSettings.hintSuffix": "。未設定時留空即可。",
  "tokenSettings.placeholder": "貼上 API Token",
  "tokenSettings.ariaLabel": "API Token 設定",

  // ── 檔案上傳 ──
  "fileUploader.selectFiles": "選擇圖片檔案",
  "fileUploader.selectDir": "選擇目錄",
  "fileUploader.fileTypeHint":
    "支援 JPG、PNG、BMP、TIFF 格式。選擇目錄時自動遍歷子目錄。",
  "fileUploader.uploading": "正在上傳... {uploaded} / {total}",
  "fileUploader.cancelUpload": "取消上傳",
  "fileUploader.uploadComplete": "上傳完成：{count} 個檔案",
  "fileUploader.skippedFiles": "跳過 {count} 個不支援的檔案",
  "fileUploader.useUploaded": "使用已上傳檔案",
  "fileUploader.confirmed": "已確認",
  "fileUploader.reselect": "重新選擇",
  "fileUploader.uploadFailed": "上傳失敗",

  // ── 目錄選擇 ──
  "dirPicker.title": "選擇輸出目錄",
  "dirPicker.currentPath": "目前路徑：",
  "dirPicker.parentDir": ".. (上級目錄)",
  "dirPicker.emptyDir": "（空目錄）",
  "dirPicker.newDirPlaceholder": "輸入新目錄名（可選，留空則使用目前目錄）",
  "dirPicker.selectWithDir": "選擇: {path}/{dir}",
  "dirPicker.selectPath": "選擇: {path}",
  "dirPicker.accessError": "無法存取目錄",

  // ── 原圖面板 ──
  "sourceImages.title": "原圖（點擊放大）",
  "sourceImages.lightboxAlt": "放大查看",

  // ── 上傳預覽 ──
  "uploadPreview.title": "上傳預覽",
  "uploadPreview.photoCount": "{count} 張照片",
  "uploadPreview.groupCount": "{count} 張",
  "uploadPreview.ungrouped": "未分組",
  "uploadPreview.noImages": "目前沒有可用圖片",
  "uploadPreview.deleting": "刪除中...",

  // ── 來源選擇（本地/伺服器） ──
  "sourcePicker.localTab": "本地",
  "sourcePicker.serverTab": "伺服器",
  "sourcePicker.currentPath": "目前路徑：",
  "sourcePicker.parentDir": ".. (上級目錄)",
  "sourcePicker.emptyDir": "（目前目錄下無圖片和子目錄）",
  "sourcePicker.useThisDir": "使用目前目錄",
  "sourcePicker.useSelectedFiles": "使用選取的 {count} 個檔案",
  "sourcePicker.confirmed": "已選取：{path}",
  "sourcePicker.reset": "重新選取",
  "sourcePicker.browseError": "瀏覽目錄失敗",
  "sourcePicker.stageError": "伺服器檔案登記失敗",
  "sourcePicker.fileCheckboxAria": "選取 {name}",
  "sourcePicker.pathPlaceholder": "直接輸入伺服器路徑（可選）",
  "sourcePicker.goPath": "跳轉",
  "sourcePicker.sizeKB": "{size} KB",
  "sourcePicker.imageCount": "{count} 張",
};
