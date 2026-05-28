/**
 * 文件上传组件：支持选择文件或上传整个目录（保留子目录结构）
 */

import { useEffect, useRef } from "react";

import { useFileUpload } from "../features/task/useFileUpload";
import { renderLocalized, useTranslation } from "../i18n";
import { UploadPreviewPanel } from "./UploadPreviewPanel";

/** 允许的图片 MIME */
const ACCEPT = "image/jpeg,image/png,image/bmp,image/tiff";

/** 允许的图片扩展名（小写） */
const ALLOWED_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".bmp",
  ".tiff",
  ".tif",
]);

interface FileUploaderProps {
  /** 上传完成后回调，传入服务端 image_dir */
  readonly onComplete: (imageDir: string) => void;
  /** 是否禁用 */
  readonly disabled: boolean;
}

/** 从 File 列表中过滤出图片文件 */
function filterImageFiles(files: File[]): File[] {
  return files.filter((f) => {
    const dot = f.name.lastIndexOf(".");
    if (dot === -1) return false;
    return ALLOWED_EXTENSIONS.has(f.name.slice(dot).toLowerCase());
  });
}

/**
 * 从 webkitRelativePath 中去掉最外层目录前缀。
 *
 * 浏览器的 webkitRelativePath 格式为 "root/sub/file.jpg"，
 * 其中 root 是用户选择的目录名。去掉它后得到 "sub/file.jpg"，
 * 这样服务端保存时就不会多一层无意义的顶层目录。
 */
function stripRootDir(relPath: string): string {
  const firstSlash = relPath.indexOf("/");
  if (firstSlash === -1) return relPath;
  return relPath.slice(firstSlash + 1);
}

export function FileUploader({
  onComplete,
  disabled,
}: FileUploaderProps): React.JSX.Element {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);
  const {
    stage,
    uploadedCount,
    totalCount,
    failedFiles,
    uploadedFiles,
    deletingFileIds,
    imageDir,
    error,
    startUpload,
    cancelUpload,
    finalizeUpload,
    deleteUploadedFile,
    reset,
  } = useFileUpload();

  const isUploading = stage === "uploading";

  /** 多文件选择（平铺，无目录结构） */
  const handleFileChange = (
    event: React.ChangeEvent<HTMLInputElement>,
  ): void => {
    const { files } = event.target;
    if (files === null || files.length === 0) return;
    const imageFiles = filterImageFiles([...files]);
    if (imageFiles.length === 0) return;
    startUpload(imageFiles);
  };

  /** 目录选择（保留子目录结构） */
  const handleDirChange = (
    event: React.ChangeEvent<HTMLInputElement>,
  ): void => {
    const { files } = event.target;
    if (files === null || files.length === 0) return;

    const allFiles = [...files];
    const imageFiles = filterImageFiles(allFiles);
    if (imageFiles.length === 0) return;

    // 提取相对路径（去掉最外层目录）
    const relativePaths = imageFiles.map((f) =>
      stripRootDir(f.webkitRelativePath || f.name),
    );

    startUpload(imageFiles, relativePaths);
  };

  const handleComplete = (): void => {
    void finalizeUpload();
  };

  useEffect(() => {
    if (imageDir !== undefined) {
      onComplete(imageDir);
    }
  }, [imageDir, onComplete]);

  return (
    <div className="file-uploader">
      {/* 文件/目录选择 */}
      {stage === "idle" && (
        <div className="upload-select">
          <div className="upload-buttons">
            <button
              type="button"
              className="btn-upload"
              disabled={disabled}
              onClick={() => fileInputRef.current?.click()}
            >
              {t("fileUploader.selectFiles")}
            </button>
            <button
              type="button"
              className="btn-upload"
              disabled={disabled}
              onClick={() => dirInputRef.current?.click()}
            >
              {t("fileUploader.selectDir")}
            </button>
          </div>
          {/* 隐藏的 file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT}
            onChange={handleFileChange}
            disabled={disabled}
            className="file-input-hidden"
          />
          {/* 隐藏的 directory input */}
          <input
            ref={dirInputRef}
            type="file"
            // @ts-expect-error -- webkitdirectory 是非标准属性，主流浏览器均支持
            webkitdirectory=""
            onChange={handleDirChange}
            disabled={disabled}
            className="file-input-hidden"
          />
          <p className="upload-hint">
            {t("fileUploader.fileTypeHint")}
          </p>
        </div>
      )}

      {/* 上传进度 */}
      {isUploading && (
        <div className="upload-progress">
          <p>
            {t("fileUploader.uploading", { uploaded: uploadedCount, total: totalCount })}
          </p>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{
                width: `${totalCount > 0 ? ((uploadedCount / totalCount) * 100).toString() : "0"}%`,
              }}
            />
          </div>
          <button
            type="button"
            className="btn-cancel-upload"
            onClick={cancelUpload}
          >
            {t("fileUploader.cancelUpload")}
          </button>
        </div>
      )}

      {/* 上传完成 */}
      {stage === "completed" && (
        <div className="upload-done">
          <p className="upload-success">
            {t("fileUploader.uploadComplete", { count: uploadedCount })}
          </p>
          {failedFiles.length > 0 && (
            <p className="upload-warn">
              {t("fileUploader.skippedFiles", { count: failedFiles.length })}
            </p>
          )}
          <UploadPreviewPanel
            files={uploadedFiles}
            deletingFileIds={deletingFileIds}
            onDelete={(fileId) => {
              void deleteUploadedFile(fileId);
            }}
          />
          <button
            type="button"
            className={`btn-use-upload${imageDir === undefined ? "" : " confirmed"}`}
            onClick={handleComplete}
            disabled={uploadedFiles.length === 0 || imageDir !== undefined}
          >
            {imageDir === undefined ? t("fileUploader.useUploaded") : t("fileUploader.confirmed")}
          </button>
          <button type="button" className="btn-reupload" onClick={reset}>
            {t("fileUploader.reselect")}
          </button>
        </div>
      )}

      {/* 上传错误 */}
      {stage === "error" && (
        <div className="upload-error">
          <p>
            {error === undefined
              ? t("fileUploader.uploadFailed")
              : renderLocalized(error, t)}
          </p>
          <button type="button" onClick={reset}>
            {t("common.retry")}
          </button>
        </div>
      )}
    </div>
  );
}
