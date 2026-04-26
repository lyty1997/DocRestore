# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""paddle_ocr_worker.py 命令分发回归守护。

历史 bug：v5 之前 main() dispatch 只把 server_url / server_model_name
透传给 handle_initialize，**漏了 pipeline 字段**。导致 EngineManager
即使把 pipeline=basic 写进 init_cmd，worker 也按默认 "vl" 加载 →
代码模式（AGE-8）需要的 PageOCR.text_lines 永远为空，files-index.json
落不了盘。

本测试用源码扫描确保 dispatch 把请求里所有 handle_initialize 形参都
正确透传，避免 silently drop 字段。
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

WORKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "paddle_ocr_worker.py"
)


def _extract_handle_initialize_signature() -> set[str]:
    """从源码抓 handle_initialize 的形参（除 self）"""
    src = WORKER_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"def handle_initialize\(\s*self,\s*([^)]+)\)",
        src, re.DOTALL,
    )
    assert m, "无法定位 handle_initialize 签名"
    params: set[str] = set()
    for piece in m.group(1).split(","):
        name = piece.split(":")[0].split("=")[0].strip()
        if name and name != "self":
            params.add(name)
    return params


def _extract_dispatch_block() -> str:
    """从 main() 抓 'cmd == initialize' 的整段 dispatch（到下一个 elif/else）"""
    src = WORKER_PATH.read_text(encoding="utf-8")
    m = re.search(
        r'if cmd == "initialize":\s*\n(.+?)(?:^\s*elif cmd|^\s*else:)',
        src, re.MULTILINE | re.DOTALL,
    )
    assert m, "无法定位 'cmd == initialize' 分发块"
    return textwrap.dedent(m.group(1))


class TestInitializeDispatchPassesAllParams:
    """dispatch 必须把 handle_initialize 的每个形参都从 request 取值传入。"""

    def test_pipeline_field_is_forwarded(self) -> None:
        """pipeline 必须出现在 dispatch 中（核心 bug 守护）"""
        block = _extract_dispatch_block()
        assert "pipeline" in block, (
            "dispatch 'cmd == initialize' 没传 pipeline 字段；"
            "EngineManager 切 basic 时 worker 仍跑 vl，AGE-8 代码模式失效。\n"
            f"dispatch:\n{block}"
        )
        assert 'request.get("pipeline"' in block, (
            "pipeline 没从 request 取值，可能是硬编码默认；"
            f"dispatch:\n{block}"
        )

    def test_all_handle_initialize_params_appear_in_dispatch(self) -> None:
        """handle_initialize 每个形参（除 self）都必须出现在 dispatch 中。

        这是更严格的回归守护：未来增加新形参时，提醒同步更新 main()。
        """
        params = _extract_handle_initialize_signature()
        block = _extract_dispatch_block()
        missing = {p for p in params if p not in block}
        assert not missing, (
            f"handle_initialize 形参 {missing} 未在 dispatch 中透传。"
            f" dispatch:\n{block}"
        )


class TestSignatureSanity:
    """handle_initialize 签名稳定性"""

    def test_signature_has_pipeline_with_default(self) -> None:
        """pipeline 必须有 default 值（向后兼容；未传时回退 vl）"""
        src = WORKER_PATH.read_text(encoding="utf-8")
        m = re.search(
            r'def handle_initialize\(\s*self,[^)]*pipeline:\s*str\s*=\s*"([^"]+)"',
            src, re.DOTALL,
        )
        assert m, "handle_initialize.pipeline 应有 default 值"
        # default 是 "vl" 因为旧文档模式默认走 vl
        assert m.group(1) == "vl"
