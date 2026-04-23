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
  "taskList.deleteItem": "刪除此任務",
  "taskList.cannotDeleteRunning": "任務執行中，請先取消再刪除",
  "taskList.deleteConfirmTitle": "刪除任務",
  "taskList.deleteConfirmMessage": "確定刪除任務 {id} 及其全部產物嗎？此操作不可恢復。",
  "taskList.clearFinished": "清理已結束",
  "taskList.clearFinishedTitle": "清理已結束任務",
  "taskList.clearFinishedMessage": "將刪除 {count} 個已完成/失敗的任務及其產物。此操作不可恢復，繼續嗎？",
  "taskList.deleteFailed": "刪除失敗",
  "taskList.clearFinishedResult": "已刪除 {ok} 個，失敗 {fail} 個",

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
  "taskForm.engineWarmup": "預載入引擎",
  "taskForm.engineWarming": "引擎載入中...",
  "taskForm.engineReady": "已就緒",
  "taskForm.engineError": "載入失敗",
  "taskForm.gpuAuto": "自動選擇（推薦）",
  "taskForm.gpuAutoWithHint": "自動（{hint}）",
  "taskForm.llmConfigExpanded": "▾ LLM 精修設定",
  "taskForm.llmConfigCollapsed": "▸ LLM 精修設定",
  "taskForm.modelName": "模型名稱",
  "taskForm.modelNamePlaceholder": "例如 openai/gpt-4o、openai/glm-5",
  "taskForm.apiBaseUrl": "API Base URL",
  "taskForm.apiBaseUrlPlaceholder": "例如 https://poloai.top/v1（必須含 /v1 等版本號）",
  "taskForm.apiBaseUrlWarning": "URL 通常以 /v1 之類的版本號結尾，目前輸入的位址可能被中轉站識別為首頁，確認繼續？",
  "taskForm.apiKey": "API Key",
  "taskForm.apiKeyPlaceholder": "留空使用伺服器預設金鑰",
  "taskForm.apiKeyToggleShow": "顯示金鑰",
  "taskForm.apiKeyToggleHide": "隱藏金鑰",
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
  "taskProgress.subtasksLabel": "並行處理 {count} 篇子文件",
  "taskProgress.phaseOcr": "OCR",
  "taskProgress.phaseLlm": "LLM 精修",
  "taskProgress.mainLabel": "主進度",
  "taskProgress.streamingCount": "第 {current} 小段（流式切分）",
  // 服務端結構化進度訊息（pipeline report_fn 下發的 message_key 對應）
  "progress.waiting": "等待開始",
  "progress.ocrPage": "OCR {current}/{total}...",
  "progress.refineStream": "流式精修 第 {index} 小段",
  "progress.refineSegment": "精修第 {current}/{total} 段...",
  "progress.gapFill": "補充缺口 {current}/{total}...",
  "progress.finalRefine": "整篇文件級精修...",
  "progress.finalRefineChunks": "整篇文件級精修...（{chunks} 塊並行）",
  "progress.docBoundary": "偵測文件邊界...",
  "progress.piiRedaction": "PII 脫敏...",
  "progress.render": "渲染輸出...",
  "progress.completed": "處理完成",
  "progress.cancelled": "任務取消",
  "progress.failed": "處理失敗",

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
  "taskDetail.docSummaryAll": "全部 {total} 個子文件已完成",
  "taskDetail.docSummaryPartial": "已完成 {done}/{total}，{failed} 個失敗",
  "taskDetail.docFailedTitle": "此子文件處理失敗",
  "taskDetail.docFailedHint": "可點擊頁首的「繼續」按鈕，複用已完成內容、僅重跑失敗部分。",
  "taskDetail.loadError": "載入任務資訊失敗",
  "taskDetail.loadingTask": "載入任務資訊...",
  "taskDetail.cancelFailed": "取消失敗",
  "taskDetail.deleteFailed": "刪除失敗",
  "taskDetail.retryFailed": "重試失敗",
  "taskDetail.retryHint": "從頭跑，不複用輸出目錄（會重新 OCR 所有圖）",
  "taskDetail.resumeTask": "繼續",
  "taskDetail.resumeHint": "複用原輸出目錄，跳過已 OCR 的圖、只補跑未完成部分",
  "taskDetail.resumeFailed": "繼續任務失敗",

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
