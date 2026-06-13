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

  // ── 文件正文裁剪（content_crop）──
  "crop.title": "正文裁剪",
  "crop.desc": "手動微調每張圖的正文裁剪框（去左右欄 / 頁首）。關閉則按自動偵測裁剪。",
  "crop.hint": "點擊縮圖切換圖片；拖動方框移動、拖四周手柄縮放，框住正文區。",
  "crop.detecting": "偵測建議裁剪框中…",
  "crop.detectFailed": "裁剪框偵測失敗，將退回自動裁剪",
  "crop.noneToCrop": "未偵測到需裁剪的圖（已是正文 / 無側欄）",
  "crop.prev": "上一張",
  "crop.next": "下一張",
  "crop.delete": "刪除此圖",
  "crop.noBoxHint": "未偵測到側欄，此圖預設不裁剪。",
  "crop.addBox": "手動框選裁剪",
  "crop.removeBox": "不裁剪此圖",
  "crop.excludedLabel": "已排除 {count} 張，本次任務跳過（點擊檔名恢復）：",

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
  "taskForm.providerLabel": "Provider",
  "taskForm.provider_cloud": "雲端 API",
  "taskForm.provider_local": "本地服務",
  "taskForm.providerHint_cloud":
    "走 litellm 呼叫雲端模型，含 LLM 實體識別（PII 增強）。需要填寫 API Key。",
  "taskForm.providerHint_local":
    "透過本地 OpenAI 相容服務（vLLM / ollama / llama.cpp）呼叫，資料不出本地；PII 實體識別略過、僅 regex 去敏。API Base 填本地位址，例如 http://localhost:11434/v1。",
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
  "taskForm.codeModeTitle": "IDE 程式碼模式",
  "taskForm.codeModeDesc":
    "上傳 IDE 編輯器截圖時啟用：輸出獨立原始檔（.cc/.h/.gn/.py/...）到 files/，不再合成單份 Markdown。",
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
  "progress.piiRedaction": "PII 脫敏...",
  "progress.render": "渲染輸出...",
  "progress.completed": "處理完成",
  "progress.cancelled": "任務取消",
  "progress.failed": "處理失敗",
  "progress.llmUnavailable":
    "LLM provider 暫不可用（{model}），已熔斷 {cool_down_s}s，相關段級精修降級為原文",

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
  "taskDetail.viewModeDoc": "文件模式",
  "taskDetail.viewModeCode": "程式碼模式",

  // ── 程式碼模式視圖（AGE-50） ──
  "codeViewer.loadingIndex": "載入檔案索引...",
  "codeViewer.indexError": "載入索引失敗",
  "codeViewer.empty": "程式碼模式未產生任何來源檔案",
  "codeViewer.filesTitle": "來源檔案 ({count})",
  "codeViewer.lines": "行",
  "codeViewer.flags": "{count} 個標記",
  "codeViewer.loadingFile": "載入檔案...",
  "codeViewer.fileError": "載入檔案失敗",
  "codeViewer.sourcePagesTitle": "原圖來源",
  "codeViewer.sourcePagesCount": "{count} 張原圖來源（點擊展開）",
  "codeViewer.noSourceImages": "無對應原圖",
  "codeViewer.editAreaLabel": "編輯程式碼檔案內容",
  "codeViewer.saveError": "儲存程式碼檔案失敗",
  "codeViewer.liveDiagnosticPending": "正在檢查...",
  "codeViewer.liveDiagnosticError": "即時語法檢查失敗",
  "codeViewer.diagnosticsTitle": "目前診斷 ({count})",
  "codeViewer.acceptDiagnostic": "接受此診斷",
  "codeViewer.clearAcceptedDiagnostics": "恢復已接受診斷",
  "codeViewer.acceptedDiagnostics": "已接受 {count} 條診斷",
  "codeViewer.compile.passed": "編譯通過",
  "codeViewer.compile.failed": "編譯失敗",
  "codeViewer.compile.skipped": "跳過編譯",

  // ── WYSIWYG 編輯器 ──
  "editor.placeholder": "在這裡編輯文件…",
  "editor.paragraph": "正文",
  "editor.h1": "標題 1",
  "editor.h2": "標題 2",
  "editor.h3": "標題 3",
  "editor.h4": "標題 4",
  "editor.bold": "加粗 (Ctrl+B)",
  "editor.italic": "斜體 (Ctrl+I)",
  "editor.strike": "刪除線",
  "editor.inlineCode": "行內代碼",
  "editor.bulletList": "無序列表",
  "editor.orderedList": "有序列表",
  "editor.blockquote": "引用",
  "editor.hr": "水平分隔線",
  "editor.insertTable": "插入表格",
  "editor.link": "連結",
  "editor.insertFigure": "插入截圖（從原圖重新框選插圖）",
  "editor.linkPrompt": "輸入 URL（留空清除連結）",
  "editor.undo": "撤銷 (Ctrl+Z)",
  "editor.redo": "重做 (Ctrl+Y)",

  // ── 重新截取插圖對話框 ──
  "figureCrop.title": "重新截取插圖",
  "figureCrop.hint": "選一張原圖，拖動方框框住要插入的插圖區域，確認後插入到游標處。",
  "figureCrop.loading": "載入原圖列表中…",
  "figureCrop.noSources": "沒有可用的原圖",
  "figureCrop.sourceLabel": "原圖",
  "figureCrop.fromCursorPage": "已自動選中游標所在頁的原圖，可在上方下拉切換",
  "figureCrop.modeRect": "矩形裁剪",
  "figureCrop.modeQuad": "四角校正",
  "figureCrop.quadHint": "把 4 個角點分別拖到插圖的左上 / 右上 / 右下 / 左下角，確認後自動旋轉 + 透視校正為正視圖。",  "figureCrop.confirm": "裁剪並插入",
  "figureCrop.submitting": "裁剪中…",

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

  // ── PPT 模式 i18n（taskForm / progress / errors 集中补充）──
  "taskForm.modeLabel": "處理模式",
  "taskForm.mode_doc": "文件模式",
  "taskForm.mode_code": "程式碼模式",
  "taskForm.mode_ppt": "PPT 模式",
  "taskForm.docModeDesc":
    "預設模式：文件照片還原為帶格式的 Markdown，跨頁自動去重合併。",
  "taskForm.pptModeDesc":
    "螢幕拍攝投影片還原為保序 Markdown：自動透視矯正，文字 + 公式（LaTeX），化學結構 / 圖表裁剪為圖片。",
  "taskForm.refineTitle": "LLM 精修",
  "taskForm.refineDesc":
    "對文件 / 程式碼 / PPT 所有模式生效：開啟則用 LLM 精修辨識結果（文件分段、PPT 按頁），關閉則只輸出 OCR 清洗文字。",
  "progress.pptPage": "PPT 模式：精修第 {current}/{total} 頁",
  "progress.pptPagePlain": "PPT 模式：處理第 {current}/{total} 頁",
  "progress.pptRender": "PPT 模式：組裝文件...",
  // ── API 業務錯誤 ──
  "errors.api.mode_conflict": "模式衝突：文件 / 程式碼 / PPT 三選一互斥",
  "errors.api.llm_api_base_rejected":
    "LLM 中轉位址（api_base）被安全策略拒絕：必須是公網 http(s) 位址，" +
    "或在伺服端白名單內（禁止私網 / 內網 / 鏈路本地位址）",
  "errors.api.output_dir_rejected":
    "輸出目錄（output_dir）被安全策略拒絕：必須落在伺服端受信工作根之下" +
    "（預設系統暫存目錄，可由 DOCRESTORE_WORK_ROOT 設定），" +
    "以防誤刪工作根外的任意目錄",
  "errors.api.unauthorized": "缺少或無效的 API Token",
  "errors.api.service_not_initialized": "服務未初始化",
  "errors.api.engine_manager_not_initialized": "OCR 引擎管理器未初始化",
  "errors.api.task_not_found": "任務不存在",
  "errors.api.task_result_not_ready": "任務尚未完成或已失敗",
  "errors.api.task_no_results": "任務尚無結果（未完成或根級錯誤）",
  "errors.api.task_action_conflict": "任務狀態衝突：{reason}",
  "errors.api.asset_not_found": "資源不存在",
  "errors.api.file_not_found": "檔案不存在",
  "errors.api.image_not_found": "圖片不存在",
  "errors.api.code_dir_not_found": "程式碼目錄不存在",
  "errors.api.files_index_not_found": "任務未產生程式碼索引（非程式碼模式或未完成）",
  "errors.api.files_index_parse_error": "程式碼索引解析失敗：{reason}",
  "errors.api.files_index_bad_format": "程式碼索引格式異常（非陣列）",
  "errors.api.read_failed": "讀取失敗：{reason}",
  "errors.api.invalid_filename": "非法檔名",
  "errors.api.markdown_update_failed": "儲存失敗：{reason}",
  "errors.api.invalid_crop_region": "裁剪區域無效（缺少或退化的框 / 四角點）",
  "errors.api.upload_session_not_found": "上傳會話不存在",
  "errors.api.upload_session_completed": "會話已完成，無法繼續操作",
  "errors.api.upload_session_no_files": "會話中無檔案",
  "errors.api.upload_file_not_found": "上傳檔案不存在",
  "errors.api.cleanup_statuses_empty": "statuses 不能為空",
  "errors.api.cleanup_statuses_invalid":
    "僅允許清理終態任務（completed / failed），非法狀態：{invalid}",
  "errors.api.stage_paths_empty": "paths 不能為空",
  "errors.api.stage_too_many_files": "單次最多 {max} 個檔案",
  "errors.api.stage_path_not_absolute": "路徑必須為絕對路徑：{path}",
  "errors.api.stage_path_unresolvable": "路徑無法解析：{path}（{reason}）",
  "errors.api.stage_path_not_file": "不是普通檔案：{path}",
  "errors.api.stage_path_bad_ext": "不支援的檔案類型：{path}",
  "errors.api.stage_symlink_failed": "建立符號連結失敗：{path} → {reason}",
  "errors.api.browse_not_dir": "路徑不是目錄：{path}",
  "errors.api.browse_permission_denied": "無權限存取：{path}",

  // ── HTTP 狀態碼診斷 hint ──
  "errors.http.413":
    "請求體過大（HTTP 413）。檢查 starlette MultiPartParser / 反向代理的 max body size。",
  "errors.http.504":
    "閘道超時（HTTP 504）。後端處理超過代理超時閾值，可調大 vite proxyTimeout 或後端 keep-alive。",
  "errors.http.5xx": "後端錯誤。查看 backend 日誌確認堆疊。",

  // ── 前端客戶端層錯誤 ──
  "errors.client.parseFailed": "回應解析失敗：非合法 JSON",
  "errors.client.parseFailedHint":
    "可能後端返回了 HTML（502/504 閘道頁）或被中介軟體改寫。",
  "errors.client.uploadNetworkFailed":
    "上傳失敗（{count} 張 / {sizeMb} MB / {elapsedMs}ms）：{detail}",
  "errors.client.uploadNetworkFailedHint":
    "瀏覽器未取得 HTTP 回應。常見原因：① Vite proxy / 反向代理超時斷流（已加大為無限，舊 dev server 需重啟才生效）；② 後端進程崩潰或 OOM（看 backend 日誌）；③ /tmp 寫滿（df -h /tmp）；④ 上傳體積超出 starlette MultiPartParser 限制。本批檔案：{filenames}。排查：F12 Network 看具體 net::ERR_*；或暫時直連 http://127.0.0.1:8000/api/v1 旁路 proxy 複測。",

  // ── 任務/上傳 hook 兜底文案 ──
  "errors.task.runFailed": "任務失敗",
  "errors.task.runFailedWithReason": "任務失敗：{reason}",
  "errors.task.createFailed": "建立任務失敗",
  "errors.upload.confirmFailed": "確認上傳失敗",
  "errors.upload.deleteFailed": "刪除圖片失敗",
  "errors.upload.noneSucceeded": "沒有檔案上傳成功",
  "errors.upload.batchFailed":
    "第 {batch}/{total} 批失敗（已成功 {uploaded}/{count}）：\n{cause}",
  "errors.unknown": "未知錯誤",
};
