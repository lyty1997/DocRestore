#!/usr/bin/env bash
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# 安装本地 NER 环境：spaCy + 中英文 CNN 模型（zh/en_core_web_md）。
# 用于 PII 人名/机构名脱敏「数据不出本机」（详见 docs/zh/backend/pii-local-ner.md）。
# 与后端「一键配置」(POST /api/v1/ner/setup) 同源；幂等（模型已装则跳过）。
#
# 用法：
#   bash scripts/setup_ner.sh            # 自动找 docrestore conda 环境
#   PYTHON=/path/to/python bash scripts/setup_ner.sh   # 指定 python

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

MODELS=("zh_core_web_md" "en_core_web_md")

# 定位后端 python：显式 $PYTHON > 已装 docrestore 的 conda 环境 > 当前 python
PY="${PYTHON:-python}"
if [[ -z "${PYTHON:-}" ]] && command -v conda &>/dev/null; then
    conda_base="$(conda info --base 2>/dev/null || true)"
    if [[ -n "$conda_base" ]]; then
        # shellcheck source=/dev/null
        source "$conda_base/etc/profile.d/conda.sh"
        for env in docrestore ppocr_client deepseek_ocr; do
            if conda env list 2>/dev/null | grep -q "^${env} " &&
                conda run -n "$env" python -c "import docrestore" 2>/dev/null; then
                PY="$(conda run -n "$env" python -c 'import sys; print(sys.executable)')"
                echo "[setup_ner] 使用 conda 环境: ${env}"
                break
            fi
        done
    fi
fi
echo "[setup_ner] python: ${PY}"

echo "[setup_ner] 安装 spaCy（.[ner] extra）..."
"$PY" -m pip install -e '.[ner]'

for m in "${MODELS[@]}"; do
    if "$PY" -c "import spacy.util as u, sys; sys.exit(0 if u.is_package('${m}') else 1)" 2>/dev/null; then
        echo "[setup_ner] 模型已安装，跳过: ${m}"
    else
        echo "[setup_ner] 下载模型: ${m}（首次约 30-80MB，走 GitHub release 可能较慢）"
        "$PY" -m spacy download "${m}"
    fi
done

echo "[setup_ner] 完成，验证模型可用性："
"$PY" -c "import spacy.util as u; print({m: u.is_package(m) for m in ['zh_core_web_md', 'en_core_web_md']})"
