#!/usr/bin/env python3
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

"""nvidia-smi 持续采样，写出 CSV 时序。

用法：
    python scripts/gpu_sampler.py --gpu-id 0 --interval-ms 500 \\
        --output output/bench/gpu_trace.csv

SIGTERM/SIGINT 信号到来时：
- 向 nvidia-smi 子进程发 SIGTERM
- 保证 CSV 写完 flush 后退出（父进程拿到完整数据）
"""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import FrameType


def _run(gpu_id: str, interval_ms: int, output: Path) -> int:
    """启动 nvidia-smi -lms 循环采样，stdout 重定向到 output CSV。"""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        print("未找到 nvidia-smi，无法采样 GPU", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        nvidia_smi,
        "--query-gpu=timestamp,index,utilization.gpu,utilization.memory,"
        "memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,nounits",
        f"--id={gpu_id}",
        f"-lms={interval_ms}",
    ]

    with output.open("w", encoding="utf-8", buffering=1) as fout:
        proc = subprocess.Popen(  # noqa: S603 — nvidia_smi 来自 shutil.which
            argv,
            stdout=fout,
            stderr=subprocess.PIPE,
        )

        def _stop(_signo: int, _frame: FrameType | None) -> None:
            """父进程发 SIGTERM/SIGINT 时转发给 nvidia-smi。"""
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        try:
            return proc.wait()
        finally:
            # 若 wait 被打断，兜底确保子进程退出
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="nvidia-smi GPU 采样器")
    parser.add_argument(
        "--gpu-id", default="0",
        help="GPU 索引（对应 nvidia-smi --id）",
    )
    parser.add_argument(
        "--interval-ms", type=int, default=500, help="采样间隔（毫秒）",
    )
    parser.add_argument("--output", required=True, help="输出 CSV 路径")
    args = parser.parse_args()

    rc = _run(args.gpu_id, args.interval_ms, Path(args.output))
    # nvidia-smi 被 SIGTERM 打断时返回非 0，不作为失败看待
    sys.exit(0 if rc in (0, -signal.SIGTERM, 143) else rc)


if __name__ == "__main__":
    main()
