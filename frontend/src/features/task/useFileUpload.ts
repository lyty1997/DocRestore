/**
 * 文件上传 hook：管理上传会话、分批上传、进度追踪
 */

import { useCallback, useRef, useState } from "react";

import {
  ApiError,
  completeUpload,
  createUploadSession,
  deleteUploadSessionFile,
  getUploadSessionFiles,
  uploadFiles,
} from "../../api/client";
import type { UploadFileItem } from "../../api/schemas";

/** 上传阶段 */
type UploadStage = "idle" | "uploading" | "completed" | "error";

/** hook 返回值 */
interface UseFileUploadReturn {
  /** 当前阶段 */
  stage: UploadStage;
  /** 已上传文件数 */
  uploadedCount: number;
  /** 总文件数 */
  totalCount: number;
  /** 上传失败的文件名 */
  failedFiles: string[];
  /** 当前上传会话 ID */
  sessionId: string | undefined;
  /** 上传完成后的文件列表 */
  uploadedFiles: UploadFileItem[];
  /** 正在删除的文件 ID */
  deletingFileIds: string[];
  /** 最终确认后的 image_dir（用于创建任务） */
  imageDir: string | undefined;
  /** 错误信息 */
  error: string | undefined;
  /** 开始上传（relativePaths 用于保留目录结构） */
  startUpload: (files: File[], relativePaths?: readonly string[]) => void;
  /** 确认使用当前上传结果 */
  finalizeUpload: () => Promise<void>;
  /** 删除单个已上传文件 */
  deleteUploadedFile: (fileId: string) => Promise<void>;
  /** 取消正在进行的上传 */
  cancelUpload: () => void;
  /** 重置状态 */
  reset: () => void;
}

/** 每批上传的文件数 */
const BATCH_SIZE = 3;

export function useFileUpload(): UseFileUploadReturn {
  const [stage, setStage] = useState<UploadStage>("idle");
  const [uploadedCount, setUploadedCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [failedFiles, setFailedFiles] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [uploadedFiles, setUploadedFiles] = useState<UploadFileItem[]>([]);
  const [deletingFileIds, setDeletingFileIds] = useState<string[]>([]);
  const [imageDir, setImageDir] = useState<string | undefined>();
  const [error, setError] = useState<string | undefined>();
  const abortRef = useRef<AbortController | undefined>(undefined);

  const reset = useCallback((): void => {
    abortRef.current?.abort();
    abortRef.current = undefined;
    setStage("idle");
    setUploadedCount(0);
    setTotalCount(0);
    setFailedFiles([]);
    setSessionId(undefined);
    setUploadedFiles([]);
    setDeletingFileIds([]);
    setImageDir(undefined);
    setError(undefined);
  }, []);

  const finalizeUpload = useCallback(async (): Promise<void> => {
    if (sessionId === undefined || uploadedFiles.length === 0) return;

    setError(undefined);
    try {
      const complete = await completeUpload(sessionId);
      setImageDir(complete.image_dir);
    } catch (error_: unknown) {
      setError(error_ instanceof Error ? error_.message : "确认上传失败");
    }
  }, [sessionId, uploadedFiles]);

  const deleteUploadedFile = useCallback(
    async (fileId: string): Promise<void> => {
      if (sessionId === undefined) return;

      setDeletingFileIds((prev) => [...prev, fileId]);
      setError(undefined);
      try {
        await deleteUploadSessionFile(sessionId, fileId);
        const nextFiles = uploadedFiles.filter((file) => file.file_id !== fileId);
        setUploadedFiles(nextFiles);
        setUploadedCount(nextFiles.length);
        if (nextFiles.length === 0) {
          setImageDir(undefined);
        }
      } catch (error_: unknown) {
        setError(error_ instanceof Error ? error_.message : "删除图片失败");
      } finally {
        setDeletingFileIds((prev) => prev.filter((id) => id !== fileId));
      }
    },
    [sessionId, uploadedFiles],
  );

  const startUpload = useCallback(
    (files: File[], relativePaths?: readonly string[]): void => {
      if (files.length === 0) return;

      reset();
      const controller = new AbortController();
      abortRef.current = controller;
      setStage("uploading");
      setTotalCount(files.length);

      void (async () => {
        try {
          const session = await createUploadSession();
          setSessionId(session.session_id);

          const allFailed: string[] = [];
          let uploaded = 0;

          const totalBatches = Math.ceil(files.length / BATCH_SIZE);
          for (let i = 0; i < files.length; i += BATCH_SIZE) {
            const batch = files.slice(i, i + BATCH_SIZE);
            const batchPaths = relativePaths?.slice(i, i + BATCH_SIZE);
            const batchIdx = Math.floor(i / BATCH_SIZE) + 1;
            try {
              const resp = await uploadFiles(
                session.session_id,
                batch,
                batchPaths,
                controller.signal,
              );
              uploaded += resp.uploaded.length;
              allFailed.push(...resp.failed);
              setUploadedCount(uploaded);
              setFailedFiles([...allFailed]);
            } catch (error_: unknown) {
              /* 取消透传到外层 try 的 AbortError 分支 */
              if (error_ instanceof DOMException && error_.name === "AbortError") {
                throw error_;
              }
              /* 给单批失败拼上"第 N/M 批 + 已成功多少"的上下文 */
              const baseMsg =
                error_ instanceof ApiError
                  ? error_.toDisplayString()
                  : (error_ instanceof Error ? error_.message : "上传失败");
              throw new Error(
                `第 ${batchIdx.toString()}/${totalBatches.toString()} 批失败` +
                  `（已成功 ${uploaded.toString()}/${files.length.toString()}）：\n${baseMsg}`,
              );
            }
          }

          if (uploaded === 0) {
            setStage("error");
            setError("没有文件上传成功");
            return;
          }

          const filesResp = await getUploadSessionFiles(session.session_id);
          setUploadedFiles(filesResp.files);
          setStage("completed");
        } catch (error_: unknown) {
          /* 用户主动取消，静默回到 idle */
          if (error_ instanceof DOMException && error_.name === "AbortError") {
            return;
          }
          setStage("error");
          /* ApiError 走结构化展示，普通 Error 直接拿 message */
          const msg =
            error_ instanceof ApiError
              ? error_.toDisplayString()
              : (error_ instanceof Error ? error_.message : "上传失败");
          setError(msg);
        } finally {
          abortRef.current = undefined;
        }
      })();
    },
    [reset],
  );

  const cancelUpload = useCallback((): void => {
    abortRef.current?.abort();
    abortRef.current = undefined;
    setStage("idle");
    setUploadedCount(0);
    setTotalCount(0);
    setFailedFiles([]);
    setSessionId(undefined);
    setError(undefined);
  }, []);

  return {
    stage,
    uploadedCount,
    totalCount,
    failedFiles,
    sessionId,
    uploadedFiles,
    deletingFileIds,
    imageDir,
    error,
    startUpload,
    cancelUpload,
    finalizeUpload,
    deleteUploadedFile,
    reset,
  };
}
