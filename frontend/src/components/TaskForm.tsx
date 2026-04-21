/**
 * 任务创建表单：统一来源选择（本地/服务器）+ 输出目录 + OCR/LLM/PII 配置
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getOcrStatus, listGpus, warmupOcrEngine } from "../api/client";
import type { GpuInfo } from "../api/schemas";
import { useTranslation } from "../i18n";
import { DirectoryPicker } from "./DirectoryPicker";
import { SourcePicker } from "./SourcePicker";

/** OCR 引擎状态 */
type EngineStatus = "idle" | "warming" | "ready" | "error";

/** localStorage 持久化的 LLM 配置 */
const LLM_STORAGE_KEY = "docrestore_llm_config";

interface StoredLLMConfig {
  model: string;
  api_base: string;
  api_key: string;
}

/** 从 localStorage 读取已保存的 LLM 配置 */
function loadLlmConfig(): StoredLLMConfig | undefined {
  try {
    const raw = localStorage.getItem(LLM_STORAGE_KEY);
    if (raw === null) return undefined;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return undefined;
    const obj = parsed as Record<string, unknown>;
    return {
      model: typeof obj.model === "string" ? obj.model : "",
      api_base: typeof obj.api_base === "string" ? obj.api_base : "",
      api_key: typeof obj.api_key === "string" ? obj.api_key : "",
    };
  } catch {
    return undefined;
  }
}

/** 保存 LLM 配置到 localStorage */
function saveLlmConfig(config: StoredLLMConfig): void {
  localStorage.setItem(LLM_STORAGE_KEY, JSON.stringify(config));
}

/** 清除已保存的 LLM 配置 */
function clearLlmConfig(): void {
  localStorage.removeItem(LLM_STORAGE_KEY);
}

/** LLM 配置（传递给后端的请求级覆盖） */
export interface LLMConfig {
  model?: string | undefined;
  api_base?: string | undefined;
  api_key?: string | undefined;
}

/** 自定义敏感词条目：word 必填，code 可选（为空时后端回退到默认占位符） */
export interface CustomSensitiveWord {
  word: string;
  code?: string | undefined;
}

/** PII 脱敏配置 */
export interface PIIConfig {
  enable: boolean;
  custom_sensitive_words?: readonly CustomSensitiveWord[] | undefined;
}

/** OCR 引擎配置 */
export interface OCRConfig {
  model: string;
  gpu_id?: string | undefined;
}

/** OCR 引擎值常量（label/desc 通过 i18n 获取） */
const OCR_ENGINE_VALUES = ["paddle-ocr/ppocr-v4", "deepseek/ocr-2"] as const;
const OCR_ENGINE_KEYS: Record<string, { label: string; desc: string }> = {
  "paddle-ocr/ppocr-v4": { label: "taskForm.paddleOcrName", desc: "taskForm.paddleOcrDesc" },
  "deepseek/ocr-2": { label: "taskForm.deepseekOcrName", desc: "taskForm.deepseekOcrDesc" },
};

/** GPU 下拉 "自动" 选项的 value；与后端 OCRConfig.gpu_id=None 对应 */
const GPU_AUTO_VALUE = "";

const DEFAULT_OCR_MODEL = "paddle-ocr/ppocr-v4";

/** 拼 "0 - RTX 4070 SUPER (12 GB)" 标签；缺信息时退化为 "GPU 0" */
function formatGpuLabel(
  info: GpuInfo | undefined,
  fallbackIndex: string,
): string {
  if (info === undefined) return `GPU ${fallbackIndex}`;
  const gib = (info.memory_total_mb / 1024).toFixed(1);
  return `${info.index} - ${info.name} (${gib} GB)`;
}

interface TaskFormProps {
  readonly onSubmit: (
    imageDir: string,
    outputDir?: string,
    llm?: LLMConfig,
    pii?: PIIConfig,
    ocr?: OCRConfig,
  ) => void;
  readonly disabled: boolean;
}

/** api_base 是否以 /v{数字} 结尾（允许末尾带斜杠）。 */
function hasVersionSuffix(apiBase: string): boolean {
  return /\/v\d+\/?$/.test(apiBase.trim());
}

export function TaskForm({ onSubmit, disabled }: TaskFormProps): React.JSX.Element {
  const { t } = useTranslation();
  /* 从 localStorage 恢复已保存的 LLM 配置 */
  const [stored] = useState(loadLlmConfig);

  const [imageDir, setImageDir] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [showDirPicker, setShowDirPicker] = useState(false);

  /* LLM 配置（有已保存值时自动填充） */
  const [showLlmConfig, setShowLlmConfig] = useState(stored !== undefined);
  const [llmModel, setLlmModel] = useState(stored?.model ?? "");
  const [llmApiBase, setLlmApiBase] = useState(stored?.api_base ?? "");
  const [llmApiKey, setLlmApiKey] = useState(stored?.api_key ?? "");
  /** 是否明文显示 API Key */
  const [showApiKey, setShowApiKey] = useState(false);
  /** 是否记住 LLM 配置 */
  const [rememberLlm, setRememberLlm] = useState(stored !== undefined);

  /* OCR 引擎选择 + 预热状态 */
  const [ocrModel, setOcrModel] = useState<string>(DEFAULT_OCR_MODEL);
  /** "" = 自动（后端 pick_best_gpu）；其余为显式物理索引 */
  const [gpuId, setGpuId] = useState<string>(GPU_AUTO_VALUE);
  const [gpus, setGpus] = useState<readonly GpuInfo[]>([]);
  const [recommendedGpu, setRecommendedGpu] = useState<string | undefined>();
  const [engineStatus, setEngineStatus] = useState<EngineStatus>("idle");
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  /* 挂载时拉取 GPU 列表 */
  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const resp = await listGpus();
        if (cancelled) return;
        setGpus(resp.gpus);
        setRecommendedGpu(resp.recommended ?? undefined);
      } catch {
        /* 后端未更新或离线时安静降级：只保留 "自动" 一项 */
      }
    };
    void load();
    return (): void => {
      cancelled = true;
    };
  }, []);

  /* 脱敏开关 + 敏感词（每条可选独立代号） */
  const [piiEnabled, setPiiEnabled] = useState(false);
  const [sensitiveWords, setSensitiveWords] = useState<CustomSensitiveWord[]>(
    [],
  );
  const [wordDraft, setWordDraft] = useState("");
  const [codeDraft, setCodeDraft] = useState("");

  /** 将当前 LLM 配置同步到 localStorage */
  const persistLlmConfig = useCallback(
    (model: string, apiBase: string, apiKey: string): void => {
      saveLlmConfig({ model, api_base: apiBase, api_key: apiKey });
    },
    [],
  );

  /** rememberLlm / LLM 字段变更时自动同步 */
  useEffect(() => {
    if (rememberLlm) {
      persistLlmConfig(llmModel, llmApiBase, llmApiKey);
    }
  }, [rememberLlm, llmModel, llmApiBase, llmApiKey, persistLlmConfig]);

  const handleToggleRemember = (checked: boolean): void => {
    setRememberLlm(checked);
    if (checked) {
      persistLlmConfig(llmModel, llmApiBase, llmApiKey);
    } else {
      clearLlmConfig();
    }
  };

  const handleAddWord = (): void => {
    const trimmedWord = wordDraft.trim();
    const trimmedCode = codeDraft.trim();
    if (trimmedWord === "") return;
    if (sensitiveWords.some((w) => w.word === trimmedWord)) return;
    setSensitiveWords((prev) => [
      ...prev,
      trimmedCode === ""
        ? { word: trimmedWord }
        : { word: trimmedWord, code: trimmedCode },
    ]);
    setWordDraft("");
    setCodeDraft("");
  };

  const handleRemoveWord = (word: string): void => {
    setSensitiveWords((prev) => prev.filter((w) => w.word !== word));
  };

  /* 挂载时查询默认引擎预热状态 */
  useEffect(() => {
    let cancelled = false;
    const check = async (): Promise<void> => {
      try {
        const s = await getOcrStatus();
        if (cancelled) return;
        /* gpuId=""（自动）时，只要模型匹配就认为是已就绪 */
        const gpuMatches = gpuId === GPU_AUTO_VALUE || s.current_gpu === gpuId;
        if (s.current_model === ocrModel && gpuMatches) {
          setEngineStatus(
            s.is_ready ? "ready" : (s.is_switching ? "warming" : "idle"),
          );
        }
      } catch {
        /* 查询失败不影响使用 */
      }
    };
    void check();
    return (): void => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- 仅挂载时查询

  /* 清理轮询定时器 */
  useEffect(() => {
    return (): void => {
      if (pollRef.current !== undefined) clearInterval(pollRef.current);
    };
  }, []);

  /** 轮询引擎状态直到就绪或超时 */
  const pollEngineReady = useCallback(
    (targetModel: string, targetGpu: string): void => {
      if (pollRef.current !== undefined) clearInterval(pollRef.current);
      const id = setInterval(() => {
        void (async (): Promise<void> => {
          try {
            const s = await getOcrStatus();
            const gpuMatches =
              targetGpu === GPU_AUTO_VALUE || s.current_gpu === targetGpu;
            if (
              s.current_model === targetModel &&
              gpuMatches &&
              s.is_ready
            ) {
              setEngineStatus("ready");
              clearInterval(id);
              pollRef.current = undefined;
            }
          } catch {
            /* 静默重试 */
          }
        })();
      }, 3000);
      pollRef.current = id;
      /* 60s 超时自动停止 */
      setTimeout(() => {
        if (pollRef.current === id) {
          clearInterval(id);
          pollRef.current = undefined;
        }
      }, 60_000);
    },
    [],
  );

  /** 预加载引擎：调 warmup API 并启动轮询 */
  const handleWarmup = useCallback((): void => {
    setEngineStatus("warming");
    void (async (): Promise<void> => {
      try {
        const resp = await warmupOcrEngine(ocrModel, gpuId);
        if (resp.status === "ready") {
          setEngineStatus("ready");
          return;
        }
        /* accepted 或 switching → 开始轮询 */
        pollEngineReady(ocrModel, gpuId);
      } catch {
        setEngineStatus("error");
      }
    })();
  }, [ocrModel, gpuId, pollEngineReady]);

  const handleSourceComplete = useCallback((dir: string): void => {
    setImageDir(dir);
  }, []);

  const handleSubmit = (): void => {
    const trimmed = imageDir.trim();
    if (trimmed === "") return;

    /* 构建 LLM 配置，全部为空时不传 */
    const model = llmModel.trim();
    const apiBase = llmApiBase.trim();
    const apiKey = llmApiKey.trim();

    /* api_base 防呆：缺 /v1 之类版本号时先弹确认，避免命中网关 SPA 首页 */
    if (apiBase !== "" && !hasVersionSuffix(apiBase)) {
      const proceed = globalThis.confirm(t("taskForm.apiBaseUrlWarning"));
      if (!proceed) return;
    }

    const llm: LLMConfig | undefined =
      model || apiBase || apiKey
        ? {
            model: model || undefined,
            api_base: apiBase || undefined,
            api_key: apiKey || undefined,
          }
        : undefined;

    const pii: PIIConfig | undefined =
      piiEnabled || sensitiveWords.length > 0
        ? {
            enable: piiEnabled,
            custom_sensitive_words:
              sensitiveWords.length > 0 ? sensitiveWords : undefined,
          }
        : undefined;

    /* OCR 引擎配置：模型或 GPU 有显式值才传；gpuId="" 表示自动（不覆盖） */
    const hasOcrOverride =
      ocrModel !== DEFAULT_OCR_MODEL || gpuId !== GPU_AUTO_VALUE;
    const ocr: OCRConfig | undefined = hasOcrOverride
      ? {
          model: ocrModel,
          gpu_id: gpuId === GPU_AUTO_VALUE ? undefined : gpuId,
        }
      : undefined;

    onSubmit(trimmed, outputDir.trim() || undefined, llm, pii, ocr);
  };

  const canSubmit = !disabled && imageDir.trim() !== "";

  return (
    <div className="task-form">
      {/* 统一来源选择：本地上传 / 服务器浏览 */}
      <div className="form-group">
        <label>{t("taskForm.sourceLabel")}</label>
        <SourcePicker
          onComplete={handleSourceComplete}
          disabled={disabled}
        />
      </div>

      {/* 输出目录 */}
      <div className="form-group">
        <label htmlFor="output-dir">{t("taskForm.outputDirLabel")}</label>
        <div className="output-dir-field">
          <input
            id="output-dir"
            type="text"
            value={outputDir}
            onChange={(event) => {
              setOutputDir(event.target.value);
            }}
            placeholder={t("taskForm.outputDirPlaceholder")}
            disabled={disabled}
          />
          <button
            type="button"
            className="btn-browse"
            onClick={() => {
              setShowDirPicker(true);
            }}
            disabled={disabled}
          >
            {t("taskForm.browse")}
          </button>
        </div>
      </div>

      {/* OCR 引擎 + GPU 选择 */}
      <div className="form-group ocr-engine-section">
        <div className="ocr-engine-row">
          <div className="ocr-engine-field">
            <label htmlFor="ocr-engine">{t("taskForm.ocrEngine")}</label>
            <select
              id="ocr-engine"
              className="ocr-engine-select"
              value={ocrModel}
              onChange={(e) => {
                setOcrModel(e.target.value);
                setEngineStatus("idle");
              }}
              disabled={disabled}
            >
              {OCR_ENGINE_VALUES.map((value) => (
                <option key={value} value={value}>
                  {t(OCR_ENGINE_KEYS[value].label)}
                </option>
              ))}
            </select>
          </div>
          <div className="ocr-gpu-field">
            <label htmlFor="gpu-select">{t("taskForm.gpu")}</label>
            <select
              id="gpu-select"
              className="gpu-select"
              value={gpuId}
              onChange={(e) => {
                setGpuId(e.target.value);
                setEngineStatus("idle");
              }}
              disabled={disabled}
            >
              <option value={GPU_AUTO_VALUE}>
                {recommendedGpu !== undefined && gpus.length > 0
                  ? t("taskForm.gpuAutoWithHint").replace(
                      "{hint}",
                      formatGpuLabel(
                        gpus.find((g) => g.index === recommendedGpu),
                        recommendedGpu,
                      ),
                    )
                  : t("taskForm.gpuAuto")}
              </option>
              {gpus.map((g) => (
                <option key={g.index} value={g.index}>
                  {formatGpuLabel(g, g.index)}
                </option>
              ))}
            </select>
          </div>
          <div className="ocr-warmup-area">
            <button
              type="button"
              className="btn-warmup"
              onClick={handleWarmup}
              disabled={disabled || engineStatus === "warming" || engineStatus === "ready"}
            >
              {engineStatus === "warming"
                ? t("taskForm.engineWarming")
                : t("taskForm.engineWarmup")}
            </button>
            <span className={`engine-status engine-status--${engineStatus}`}>
              {engineStatus === "ready" && t("taskForm.engineReady")}
              {engineStatus === "error" && t("taskForm.engineError")}
            </span>
          </div>
        </div>
        <p className="ocr-engine-hint">
          {t(OCR_ENGINE_KEYS[ocrModel]?.desc ?? "")}
        </p>
      </div>

      {/* LLM 配置 */}
      <div className="form-group llm-config-section">
        <button
          type="button"
          className="llm-toggle-btn"
          onClick={() => {
            setShowLlmConfig((prev) => !prev);
          }}
          disabled={disabled}
        >
          {showLlmConfig ? t("taskForm.llmConfigExpanded") : t("taskForm.llmConfigCollapsed")}
        </button>

        {showLlmConfig && (
          <div className="llm-config-fields">
            <div className="llm-field">
              <label htmlFor="llm-model">{t("taskForm.modelName")}</label>
              <input
                id="llm-model"
                type="text"
                value={llmModel}
                onChange={(e) => {
                  setLlmModel(e.target.value);
                }}
                placeholder={t("taskForm.modelNamePlaceholder")}
                disabled={disabled}
              />
            </div>
            <div className="llm-field">
              <label htmlFor="llm-api-base">{t("taskForm.apiBaseUrl")}</label>
              <input
                id="llm-api-base"
                type="text"
                value={llmApiBase}
                onChange={(e) => {
                  setLlmApiBase(e.target.value);
                }}
                placeholder={t("taskForm.apiBaseUrlPlaceholder")}
                disabled={disabled}
              />
            </div>
            <div className="llm-field">
              <label htmlFor="llm-api-key">{t("taskForm.apiKey")}</label>
              <div className="api-key-input">
                <input
                  id="llm-api-key"
                  type={showApiKey ? "text" : "password"}
                  value={llmApiKey}
                  onChange={(e) => {
                    setLlmApiKey(e.target.value);
                  }}
                  placeholder={t("taskForm.apiKeyPlaceholder")}
                  disabled={disabled}
                />
                <button
                  type="button"
                  className="btn-toggle-key"
                  onClick={() => {
                    setShowApiKey((prev) => !prev);
                  }}
                  disabled={disabled}
                >
                  {showApiKey
                    ? t("taskForm.apiKeyToggleHide")
                    : t("taskForm.apiKeyToggleShow")}
                </button>
              </div>
            </div>
            <label className="llm-remember" htmlFor="llm-remember">
              <input
                id="llm-remember"
                type="checkbox"
                checked={rememberLlm}
                onChange={(e) => {
                  handleToggleRemember(e.target.checked);
                }}
                disabled={disabled}
              />
              {t("taskForm.rememberConfig")}
            </label>
            <p className="llm-hint">
              {t("taskForm.llmHint")}
              {rememberLlm && t("taskForm.storageWarning")}
            </p>
          </div>
        )}
      </div>

      {/* 脱敏功能 */}
      <div className="form-group pii-section">
        <div className="pii-header">
          <span className="pii-title">{t("taskForm.piiTitle")}</span>
          <label className="toggle-switch" htmlFor="pii-toggle">
            <input
              id="pii-toggle"
              type="checkbox"
              checked={piiEnabled}
              onChange={(e) => {
                setPiiEnabled(e.target.checked);
              }}
              disabled={disabled}
            />
            <span className="toggle-slider" />
            <span className="toggle-label">
              {piiEnabled ? t("common.enabled") : t("common.disabled")}
            </span>
          </label>
        </div>

        <p className="pii-desc">
          {t("taskForm.piiDesc")}
        </p>

        {/* 自定义敏感词（word + 可选代号） */}
        <div className="sensitive-word-input">
          <input
            type="text"
            value={wordDraft}
            onChange={(e) => {
              setWordDraft(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAddWord();
              }
            }}
            placeholder={t("taskForm.piiWordPlaceholder")}
            disabled={disabled}
          />
          <input
            type="text"
            className="sensitive-word-code"
            value={codeDraft}
            onChange={(e) => {
              setCodeDraft(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAddWord();
              }
            }}
            placeholder={t("taskForm.piiCodePlaceholder")}
            disabled={disabled}
          />
          <button
            type="button"
            className="btn-add-word"
            onClick={handleAddWord}
            disabled={disabled || wordDraft.trim() === ""}
          >
            {t("taskForm.piiWordAdd")}
          </button>
        </div>
        {sensitiveWords.length > 0 && (
          <div className="sensitive-word-tags">
            {sensitiveWords.map((entry) => (
              <span key={entry.word} className="word-tag">
                {entry.word}
                {entry.code !== undefined && entry.code !== "" && (
                  <span className="word-tag-code">→ {entry.code}</span>
                )}
                <button
                  type="button"
                  className="word-tag-remove"
                  onClick={() => {
                    handleRemoveWord(entry.word);
                  }}
                  disabled={disabled}
                  aria-label={t("taskForm.piiWordRemove", { word: entry.word })}
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={handleSubmit}
        disabled={!canSubmit}
      >
        {t("taskForm.startProcessing")}
      </button>

      {/* 目录选择器弹窗 */}
      {showDirPicker && (
        <DirectoryPicker
          initialPath={outputDir || undefined}
          onSelect={(path) => {
            setOutputDir(path);
            setShowDirPicker(false);
          }}
          onClose={() => {
            setShowDirPicker(false);
          }}
        />
      )}
    </div>
  );
}
