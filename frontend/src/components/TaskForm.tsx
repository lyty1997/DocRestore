/**
 * 任务创建表单：输入图片目录路径
 */

import { useRef, useState } from "react";

interface TaskFormProps {
  onSubmit: (imageDir: string, outputDir?: string) => void;
  disabled: boolean;
}

export function TaskForm({ onSubmit, disabled }: TaskFormProps): React.JSX.Element {
  const [imageDir, setImageDir] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const formRef = useRef<HTMLFormElement>(null);

  return (
    <form
      ref={formRef}
      action={() => {
        const trimmed = imageDir.trim();
        if (trimmed === "") return;
        onSubmit(trimmed, outputDir.trim() || undefined);
      }}
      className="task-form"
    >
      <div className="form-group">
        <label htmlFor="image-dir">图片目录路径（必填）</label>
        <input
          id="image-dir"
          type="text"
          value={imageDir}
          onChange={(event) => { setImageDir(event.target.value); }}
          placeholder="/path/to/images"
          disabled={disabled}
          required
        />
      </div>
      <div className="form-group">
        <label htmlFor="output-dir">输出目录路径（可选）</label>
        <input
          id="output-dir"
          type="text"
          value={outputDir}
          onChange={(event) => { setOutputDir(event.target.value); }}
          placeholder="留空则使用默认路径"
          disabled={disabled}
        />
      </div>
      <button type="submit" disabled={disabled || imageDir.trim() === ""}>
        开始处理
      </button>
    </form>
  );
}
