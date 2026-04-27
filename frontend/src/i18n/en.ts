/**
 * English translations
 *
 * Type constraint: must implement all keys from zhCN.
 * Missing any key will cause a compile error.
 */

import type { TranslationKey } from "./zh-CN";

export const en: Record<TranslationKey, string> = {
  // ── Common ──
  "common.cancel": "Cancel",
  "common.confirm": "Confirm",
  "common.delete": "Delete",
  "common.save": "Save",
  "common.saving": "Saving...",
  "common.saveFailed": "Save failed",
  "common.retry": "Retry",
  "common.loading": "Loading...",
  "common.close": "Close",
  "common.clear": "Clear",
  "common.enabled": "Enabled",
  "common.disabled": "Disabled",
  "common.preview": "Preview",
  "common.edit": "Edit",
  "common.dateLocale": "en-US",

  // ── Status ──
  "status.pending": "Pending",
  "status.processing": "Processing",
  "status.completed": "Completed",
  "status.failed": "Failed",

  // ── Sidebar ──
  "sidebar.newTask": "New Task",
  "sidebar.expandSidebar": "Expand sidebar",
  "sidebar.collapseSidebar": "Collapse sidebar",
  "sidebar.expand": "Expand",
  "sidebar.collapse": "Collapse",
  "sidebar.switchDayMode": "Switch to light mode",
  "sidebar.switchNightMode": "Switch to dark mode",
  "sidebar.dayMode": "☀ Light",
  "sidebar.nightMode": "☾ Dark",
  "sidebar.apiTokenSettings": "API Token Settings",
  "sidebar.apiToken": "API Token",
  "sidebar.resizeSidebar": "Resize sidebar",

  // ── Task list ──
  "taskList.title": "Tasks",
  "taskList.empty": "No tasks yet",
  "taskList.loadMore": "Load More",
  "taskList.deleteItem": "Delete this task",
  "taskList.cannotDeleteRunning": "Task is running — cancel it first",
  "taskList.deleteConfirmTitle": "Delete task",
  "taskList.deleteConfirmMessage": "Delete task {id} and all its artifacts? This cannot be undone.",
  "taskList.clearFinished": "Clear finished",
  "taskList.clearFinishedTitle": "Clear finished tasks",
  "taskList.clearFinishedMessage": "This will delete {count} completed/failed task(s) and their artifacts. This cannot be undone. Continue?",
  "taskList.deleteFailed": "Delete failed",
  "taskList.clearFinishedResult": "Deleted {ok}, failed {fail}",

  // ── Task form ──
  "taskForm.sourceLabel": "Image Source",
  "taskForm.outputDirLabel": "Output Directory",
  "taskForm.outputDirPlaceholder":
    "Leave empty for auto-generated path (/tmp/docrestore_xxx)",
  "taskForm.browse": "Browse...",
  "taskForm.ocrEngine": "OCR Engine",
  "taskForm.gpu": "GPU",
  "taskForm.paddleOcrName": "PaddleOCR-VL (Recommended)",
  "taskForm.paddleOcrDesc":
    "Lightweight and fast, suitable for standard documents, runs on CPU/GPU",
  "taskForm.deepseekOcrName": "DeepSeek-OCR-2",
  "taskForm.deepseekOcrDesc":
    "High-precision large model, suitable for complex layouts, requires GPU",
  "taskForm.engineWarmup": "Preload Engine",
  "taskForm.engineWarming": "Loading...",
  "taskForm.engineReady": "Ready",
  "taskForm.engineError": "Load Failed",
  "taskForm.gpuAuto": "Auto (recommended)",
  "taskForm.gpuAutoWithHint": "Auto ({hint})",
  "taskForm.llmConfigExpanded": "▾ LLM Refinement Settings",
  "taskForm.llmConfigCollapsed": "▸ LLM Refinement Settings",
  "taskForm.providerLabel": "Provider",
  "taskForm.provider_cloud": "Cloud API",
  "taskForm.provider_local": "Local Service",
  "taskForm.providerHint_cloud":
    "Call cloud models via litellm, including LLM entity detection (PII enhancement). API Key required.",
  "taskForm.providerHint_local":
    "Call local OpenAI-compatible services (vLLM / ollama / llama.cpp); data stays local. LLM entity detection is skipped, only regex redaction runs. Set API Base to a local URL, e.g. http://localhost:11434/v1.",
  "taskForm.modelName": "Model Name",
  "taskForm.modelNamePlaceholder": "e.g. openai/gpt-4o, openai/glm-5",
  "taskForm.apiBaseUrl": "API Base URL",
  "taskForm.apiBaseUrlPlaceholder": "e.g. https://poloai.top/v1 (must include /v1 or similar)",
  "taskForm.apiBaseUrlWarning": "The URL usually ends with /v1 or a similar version segment; otherwise the gateway may return its home page. Continue anyway?",
  "taskForm.apiKey": "API Key",
  "taskForm.apiKeyPlaceholder": "Leave empty to use server default key",
  "taskForm.apiKeyToggleShow": "Show key",
  "taskForm.apiKeyToggleHide": "Hide key",
  "taskForm.rememberConfig": "Remember settings",
  "taskForm.llmHint":
    "Leave empty to skip LLM refinement and output raw OCR results. Model name uses litellm format (provider/model).",
  "taskForm.storageWarning":
    " Settings (including API Key) are stored in plaintext in your browser.",
  "taskForm.codeModeTitle": "IDE Code Mode",
  "taskForm.codeModeDesc":
    "Enable when uploading IDE editor screenshots; output independent source files (.cc/.h/.gn/.py/...) under files/ instead of merged Markdown.",
  "taskForm.piiTitle": "Privacy Redaction",
  "taskForm.piiDesc":
    "When enabled, automatically detects and masks phone numbers, emails, ID numbers, and other PII. Custom sensitive words can also be added and work even when this is off.",
  "taskForm.piiWordPlaceholder": "Enter a sensitive word, press Enter to add",
  "taskForm.piiCodePlaceholder": "Code (optional, falls back to default placeholder)",
  "taskForm.piiWordAdd": "Add",
  "taskForm.piiWordRemove": "Remove {word}",
  "taskForm.startProcessing": "Start Processing",

  // ── Task progress ──
  "taskProgress.stageInit": "Engine Init",
  "taskProgress.stageOcr": "OCR Recognition",
  "taskProgress.stageClean": "Text Cleaning",
  "taskProgress.stageMerge": "Dedup & Merge",
  "taskProgress.stageRefine": "LLM Refinement",
  "taskProgress.stageRender": "Render Output",
  "taskProgress.waiting": "Waiting to start",
  "taskProgress.taskLabel": "Task: {taskId}",
  "taskProgress.polling": "Polling",
  "taskProgress.subtasksLabel": "Processing {count} subdocuments in parallel",
  "taskProgress.phaseOcr": "OCR",
  "taskProgress.phaseLlm": "LLM Refine",
  "taskProgress.mainLabel": "Main",
  "taskProgress.streamingCount": "Sub-segment {current} (streaming)",
  // Structured progress messages emitted by backend pipeline.
  "progress.waiting": "Waiting to start",
  "progress.ocrPage": "OCR {current}/{total}...",
  "progress.refineStream": "Streaming refine, segment {index}",
  "progress.refineSegment": "Refining segment {current}/{total}...",
  "progress.gapFill": "Filling gap {current}/{total}...",
  "progress.finalRefine": "Final document refine...",
  "progress.finalRefineChunks": "Final document refine... ({chunks} chunks in parallel)",
  "progress.docBoundary": "Detecting document boundaries...",
  "progress.piiRedaction": "PII redaction...",
  "progress.render": "Rendering output...",
  "progress.completed": "Completed",
  "progress.cancelled": "Task cancelled",
  "progress.failed": "Failed",
  "progress.llmUnavailable":
    "LLM provider unavailable ({model}); circuit broken for {cool_down_s}s, affected segments fell back to raw",

  // ── Task result ──
  "taskResult.title": "Results",
  "taskResult.downloadZip": "Download Results (zip)",
  "taskResult.docTab": "Document {index}",
  "taskResult.processNew": "Process New Document",
  "taskResult.resetBtn": "Start Over",

  // ── Task detail ──
  "taskDetail.title": "Task Detail",
  "taskDetail.idLabel": "ID: {taskId}",
  "taskDetail.cancelTask": "Cancel Task",
  "taskDetail.cancelConfirm":
    "Are you sure you want to cancel task {taskId}?",
  "taskDetail.deleteTask": "Delete Task",
  "taskDetail.deleteConfirm":
    "Are you sure you want to delete task {taskId} and all its outputs? This action cannot be undone.",
  "taskDetail.downloadZip": "Download Results (zip)",
  "taskDetail.errorLabel": "Error: ",
  "taskDetail.loadingResults": "Loading results...",
  "taskDetail.docPreview": "Document Preview",
  "taskDetail.noResults": "No results available",
  "taskDetail.docSummaryAll": "All {total} sub-documents completed",
  "taskDetail.docSummaryPartial": "Completed {done}/{total}, {failed} failed",
  "taskDetail.docFailedTitle": "This sub-document failed to process",
  "taskDetail.docFailedHint": "Click \"Resume\" in the header to reuse completed parts and retry only the failures.",
  "taskDetail.loadError": "Failed to load task info",
  "taskDetail.loadingTask": "Loading task info...",
  "taskDetail.cancelFailed": "Cancel failed",
  "taskDetail.deleteFailed": "Delete failed",
  "taskDetail.retryFailed": "Retry failed",
  "taskDetail.retryHint": "Start from scratch (no output-dir reuse, re-OCRs every image)",
  "taskDetail.resumeTask": "Resume",
  "taskDetail.resumeHint": "Reuse the original output dir; skip images already OCR'd and only finish the rest",
  "taskDetail.resumeFailed": "Resume failed",
  "taskDetail.viewModeDoc": "Document",
  "taskDetail.viewModeCode": "Code",

  // ── Code-mode viewer (AGE-50) ──
  "codeViewer.loadingIndex": "Loading file index...",
  "codeViewer.indexError": "Failed to load index",
  "codeViewer.empty": "No source files were generated in code mode",
  "codeViewer.filesTitle": "Source files ({count})",
  "codeViewer.lines": "lines",
  "codeViewer.flags": "{count} flag(s)",
  "codeViewer.loadingFile": "Loading file...",
  "codeViewer.fileError": "Failed to load file",
  "codeViewer.sourcePagesTitle": "Source pages",
  "codeViewer.sourcePagesCount": "{count} source pages (click to expand)",
  "codeViewer.noSourceImages": "No matching source image",
  "codeViewer.compile.passed": "Compile OK",
  "codeViewer.compile.failed": "Compile failed",
  "codeViewer.compile.skipped": "Compile skipped",

  // ── WYSIWYG editor ──
  "editor.placeholder": "Edit the document here…",
  "editor.paragraph": "Body",
  "editor.h1": "Heading 1",
  "editor.h2": "Heading 2",
  "editor.h3": "Heading 3",
  "editor.h4": "Heading 4",
  "editor.bold": "Bold (Ctrl+B)",
  "editor.italic": "Italic (Ctrl+I)",
  "editor.strike": "Strikethrough",
  "editor.inlineCode": "Inline code",
  "editor.bulletList": "Bullet list",
  "editor.orderedList": "Numbered list",
  "editor.blockquote": "Blockquote",
  "editor.hr": "Horizontal rule",
  "editor.insertTable": "Insert table",
  "editor.link": "Link",
  "editor.linkPrompt": "Enter URL (leave empty to remove)",
  "editor.undo": "Undo (Ctrl+Z)",
  "editor.redo": "Redo (Ctrl+Y)",

  // ── App-level ──
  "app.processingFailed": "Processing Failed",
  "app.unknownError": "Unknown error",

  // ── Back to top ──
  "backToTop.label": "Back to top",

  // ── Token settings ──
  "tokenSettings.title": "API Token",
  "tokenSettings.hintPrefix": "Corresponds to server environment variable ",
  "tokenSettings.hintSuffix": ". Leave empty if not configured.",
  "tokenSettings.placeholder": "Paste API Token",
  "tokenSettings.ariaLabel": "API Token Settings",

  // ── File uploader ──
  "fileUploader.selectFiles": "Select Image Files",
  "fileUploader.selectDir": "Select Directory",
  "fileUploader.fileTypeHint":
    "Supports JPG, PNG, BMP, TIFF formats. Selecting a directory traverses subdirectories automatically.",
  "fileUploader.uploading": "Uploading... {uploaded} / {total}",
  "fileUploader.cancelUpload": "Cancel Upload",
  "fileUploader.uploadComplete": "Upload complete: {count} files",
  "fileUploader.skippedFiles": "Skipped {count} unsupported files",
  "fileUploader.useUploaded": "Use Uploaded Files",
  "fileUploader.confirmed": "Confirmed",
  "fileUploader.reselect": "Re-select",
  "fileUploader.uploadFailed": "Upload failed",

  // ── Directory picker ──
  "dirPicker.title": "Select Output Directory",
  "dirPicker.currentPath": "Current path: ",
  "dirPicker.parentDir": ".. (Parent directory)",
  "dirPicker.emptyDir": "(Empty directory)",
  "dirPicker.newDirPlaceholder":
    "Enter new directory name (optional, leave empty for current directory)",
  "dirPicker.selectWithDir": "Select: {path}/{dir}",
  "dirPicker.selectPath": "Select: {path}",
  "dirPicker.accessError": "Cannot access directory",

  // ── Source images ──
  "sourceImages.title": "Source Images (click to enlarge)",
  "sourceImages.lightboxAlt": "Enlarged view",

  // ── Upload preview ──
  "uploadPreview.title": "Upload Preview",
  "uploadPreview.photoCount": "{count} photos",
  "uploadPreview.groupCount": "{count} files",
  "uploadPreview.ungrouped": "Ungrouped",
  "uploadPreview.noImages": "No images available",
  "uploadPreview.deleting": "Deleting...",

  // ── Source picker (local / server) ──
  "sourcePicker.localTab": "Local",
  "sourcePicker.serverTab": "Server",
  "sourcePicker.currentPath": "Current path: ",
  "sourcePicker.parentDir": ".. (Parent directory)",
  "sourcePicker.emptyDir": "(No images or subdirectories here)",
  "sourcePicker.useThisDir": "Use this directory",
  "sourcePicker.useSelectedFiles": "Use {count} selected file(s)",
  "sourcePicker.confirmed": "Selected: {path}",
  "sourcePicker.reset": "Re-select",
  "sourcePicker.browseError": "Failed to browse directory",
  "sourcePicker.stageError": "Failed to register server files",
  "sourcePicker.fileCheckboxAria": "Select {name}",
  "sourcePicker.pathPlaceholder": "Jump to a server path (optional)",
  "sourcePicker.goPath": "Go",
  "sourcePicker.sizeKB": "{size} KB",
  "sourcePicker.imageCount": "{count} images",

  // ── API business errors ──
  "errors.api.unauthorized": "Missing or invalid API Token",
  "errors.api.service_not_initialized": "Service not initialized",
  "errors.api.engine_manager_not_initialized": "OCR engine manager not initialized",
  "errors.api.task_not_found": "Task not found",
  "errors.api.task_result_not_ready": "Task is not completed or has failed",
  "errors.api.task_no_results": "Task has no results yet (not completed or root error)",
  "errors.api.task_action_conflict": "Task action conflict: {reason}",
  "errors.api.asset_not_found": "Asset not found",
  "errors.api.file_not_found": "File not found",
  "errors.api.image_not_found": "Image not found",
  "errors.api.code_dir_not_found": "Code directory not found",
  "errors.api.files_index_not_found":
    "Files index not generated (non-code mode or not finished)",
  "errors.api.files_index_parse_error": "Failed to parse files index: {reason}",
  "errors.api.files_index_bad_format": "Files index format invalid (not an array)",
  "errors.api.read_failed": "Read failed: {reason}",
  "errors.api.invalid_filename": "Invalid filename",
  "errors.api.markdown_update_failed": "Save failed: {reason}",
  "errors.api.upload_session_not_found": "Upload session not found",
  "errors.api.upload_session_completed": "Session is finalized, cannot continue",
  "errors.api.upload_session_no_files": "Session has no files",
  "errors.api.upload_file_not_found": "Upload file not found",
  "errors.api.cleanup_statuses_empty": "statuses must not be empty",
  "errors.api.cleanup_statuses_invalid":
    "Only completed / failed tasks may be cleaned up; invalid: {invalid}",
  "errors.api.stage_paths_empty": "paths must not be empty",
  "errors.api.stage_too_many_files": "At most {max} files per request",
  "errors.api.stage_path_not_absolute": "Path must be absolute: {path}",
  "errors.api.stage_path_unresolvable": "Path cannot be resolved: {path} ({reason})",
  "errors.api.stage_path_not_file": "Not a regular file: {path}",
  "errors.api.stage_path_bad_ext": "Unsupported file type: {path}",
  "errors.api.stage_symlink_failed": "Failed to create symlink: {path} → {reason}",
  "errors.api.browse_not_dir": "Path is not a directory: {path}",
  "errors.api.browse_permission_denied": "Permission denied: {path}",

  // ── HTTP status diagnostic hints ──
  "errors.http.413":
    "Request body too large (HTTP 413). Check starlette MultiPartParser / reverse proxy max body size.",
  "errors.http.504":
    "Gateway timeout (HTTP 504). Backend exceeded proxy timeout; tune vite proxyTimeout or backend keep-alive.",
  "errors.http.5xx": "Backend error. Inspect backend logs for the stack trace.",

  // ── Client-side errors ──
  "errors.client.parseFailed": "Response parse failed: not valid JSON",
  "errors.client.parseFailedHint":
    "Backend may have returned HTML (502/504 gateway page) or been rewritten by middleware.",
  "errors.client.uploadNetworkFailed":
    "Upload failed ({count} files / {sizeMb} MB / {elapsedMs}ms): {detail}",
  "errors.client.uploadNetworkFailedHint":
    "Browser did not receive an HTTP response. Common causes: ① Vite proxy / reverse proxy timed out (raised to unlimited; older dev server needs restart); ② backend crashed or OOM (check backend logs); ③ /tmp full (df -h /tmp); ④ upload exceeded starlette MultiPartParser limit. Files in batch: {filenames}. Debug: open F12 Network for the exact net::ERR_*, or bypass the proxy via http://127.0.0.1:8000/api/v1.",

  // ── Task / upload hook fallback ──
  "errors.task.runFailed": "Task failed",
  "errors.task.runFailedWithReason": "Task failed: {reason}",
  "errors.task.createFailed": "Failed to create task",
  "errors.upload.confirmFailed": "Failed to confirm upload",
  "errors.upload.deleteFailed": "Failed to delete image",
  "errors.upload.noneSucceeded": "No files uploaded successfully",
  "errors.upload.batchFailed":
    "Batch {batch}/{total} failed (succeeded {uploaded}/{count}):\n{cause}",
  "errors.unknown": "Unknown error",
};
