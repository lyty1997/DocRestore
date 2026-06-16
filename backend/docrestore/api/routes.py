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

"""REST/WS 路由定义"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import logging
import zipfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, Response
from starlette.websockets import WebSocketDisconnect

from docrestore.api.errors import APIErrorCode, ApiBusinessError
from docrestore.api.url_guard import validate_outbound_api_base

from docrestore.api.auth import require_auth_ws

from docrestore.api.schemas import (
    ActionResponse,
    BrowseDirsResponse,
    CodeDiagnosticResponse,
    CreateTaskRequest,
    CropBox,
    CropDetectItem,
    CropDetectRequest,
    CropDetectResponse,
    CropFigureRequest,
    CropFigureResponse,
    CropQuad,
    CustomSensitiveWord,
    DiagnoseCodeFileRequest,
    DirEntry,
    GPUInfoResponse,
    GPUListResponse,
    NERSetupStatusResponse,
    NERStatusResponse,
    OCRStatusResponse,
    OCRWarmupRequest,
    ProgressResponse,
    SourceImagesResponse,
    StageServerSourceRequest,
    StageServerSourceResponse,
    TaskCleanupRequest,
    TaskCleanupResponse,
    TaskListItem,
    TaskListResponse,
    TaskResponse,
    TaskResultResponse,
    TaskResultsResponse,
    UpdateCodeFileRequest,
    UpdateMarkdownRequest,
)
from docrestore.ocr.gpu_detect import list_gpus, pick_best_gpu
from docrestore.models import TaskProgress
from docrestore.pipeline.config import (
    CodeRestoreConfig,
    CustomWord,
    LLMConfig,
    OCRConfig,
    PIIConfig,
    PowerPointRestoreConfig,
)
from docrestore.pipeline.path_guard import (
    OutputDirRejected,
    validate_output_dir,
)
from docrestore.privacy.ner import probe_availability
from docrestore.processing.content_crop import (
    DegenerateQuadError,
    crop_quad_to_images,
    crop_region_to_images,
    detect_boxes_for_dir,
)

if TYPE_CHECKING:
    from docrestore.ocr.engine_manager import EngineManager
    from docrestore.pipeline.task_manager import TaskManager
    from docrestore.privacy.ner_install import NERSetupManager

logger = logging.getLogger(__name__)

router = APIRouter()
ws_router = APIRouter()  # WebSocket 路由（不挂 HTTP 认证，WS 用 require_auth_ws）

# 由 app.py 在 lifespan 中注入
_task_manager: TaskManager | None = None


def set_task_manager(manager: TaskManager | None) -> None:
    """注入 TaskManager 实例。

    测试中允许传入 None 以清理全局状态。
    """
    global _task_manager  # noqa: PLW0603
    _task_manager = manager


def _get_manager() -> TaskManager:
    """获取 TaskManager，未初始化时报 500"""
    if _task_manager is None:
        raise ApiBusinessError(
            APIErrorCode.SERVICE_NOT_INITIALIZED, 500, "服务未初始化",
        )
    return _task_manager


def _to_custom_words(
    raw: list[CustomSensitiveWord] | list[str],
) -> list[CustomWord]:
    """将 API 层敏感词列表（字符串或对象）转换为 CustomWord dataclass。

    兼容旧式纯字符串列表与新的 {word, code?} 对象列表；code 空串和 None 等价。
    """
    result: list[CustomWord] = []
    for item in raw:
        if isinstance(item, str):
            if item:
                result.append(CustomWord(word=item))
        else:
            word = item.word
            if word:
                result.append(
                    CustomWord(word=word, code=item.code or ""),
                )
    return result




def _validate_asset_path(asset_path: str) -> PurePosixPath | None:
    """校验 assets 相对路径。

    允许：document.md / images/** / {subdir}/document.md / {subdir}/images/**
    """
    if not asset_path:
        return None

    p = PurePosixPath(asset_path)

    # 禁止绝对路径与路径穿越
    if p.is_absolute() or ".." in p.parts or "." in p.parts:
        return None

    # 白名单：document.md（根目录或子目录下）
    if p.name == "document.md" and len(p.parts) <= 2:
        return p

    # 白名单：images/**（根目录或子目录下）
    if "images" in p.parts:
        idx = list(p.parts).index("images")
        # images 在根目录或第一层子目录下
        if idx <= 1:
            return p

    return None


def _resolve_asset_path(output_dir: Path, rel_path: PurePosixPath) -> Path | None:
    """将相对路径解析到 output_dir 下，并确保不越界（含软链接穿越防护）。"""
    try:
        root = output_dir.resolve(strict=False)
        target = (output_dir / Path(*rel_path.parts)).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None

    if not target.is_relative_to(root):
        return None

    return target


def _build_result_zip_bytes(output_dir: Path, doc_dirs: list[str]) -> bytes:
    """打包任务结果为 zip 字节。

    单文档（doc_dirs 为空或只有空字符串）：document.md + images/
    多文档：{doc_dir}/document.md + {doc_dir}/images/ × N
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 确定要打包的子目录列表
        dirs_to_pack = [d for d in doc_dirs if d] if doc_dirs else []

        if not dirs_to_pack:
            # 单文档：根目录
            _add_doc_to_zip(zf, output_dir, "")
        else:
            for d in dirs_to_pack:
                _add_doc_to_zip(zf, output_dir / d, d)

    return buf.getvalue()


#: 代码模式额外打包内容：
#: - ``files/`` 整树（还原出来的源文件）
#: - ``files-index.json``（路径 + 行号 + 来源页 + flags 索引）
#: - ``.quality_report.json``（debug，便于离线复盘）
_CODE_MODE_EXTRA_FILES: tuple[str, ...] = (
    "files-index.json",
    ".quality_report.json",
)
_CODE_MODE_EXTRA_DIRS: tuple[str, ...] = ("files",)


def _add_doc_to_zip(
    zf: zipfile.ZipFile,
    doc_dir: Path,
    prefix: str,
) -> None:
    """将单个文档目录写入 zip：
    - 文档模式：``document.md`` + ``images/``
    - 代码模式叠加：``files/`` 整树 + ``files-index.json``（+ debug 报告）
    """
    doc_path = doc_dir / "document.md"
    if doc_path.exists():
        arcname = f"{prefix}/document.md" if prefix else "document.md"
        zf.write(doc_path, arcname=arcname)

    _add_subtree_to_zip(zf, doc_dir, "images", prefix)

    # 代码模式产物（仅在存在时写入；非代码模式静默跳过）
    for extra_dir in _CODE_MODE_EXTRA_DIRS:
        _add_subtree_to_zip(zf, doc_dir, extra_dir, prefix)
    for extra_file in _CODE_MODE_EXTRA_FILES:
        p = doc_dir / extra_file
        if p.is_file():
            arcname = f"{prefix}/{extra_file}" if prefix else extra_file
            zf.write(p, arcname=arcname)


def _add_subtree_to_zip(
    zf: zipfile.ZipFile,
    doc_dir: Path,
    subdir: str,
    prefix: str,
) -> None:
    """把 ``doc_dir/subdir/`` 整树写入 zip（保持相对路径）。不存在则跳过。"""
    sub_root = doc_dir / subdir
    if not sub_root.is_dir():
        return
    for p in sorted(sub_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(doc_dir).as_posix()
        arcname = f"{prefix}/{rel}" if prefix else rel
        zf.write(p, arcname=arcname)


def _build_task_response(task_id: str) -> TaskResponse:
    """构建 TaskResponse（复用逻辑）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    progress = None
    if task.progress is not None:
        progress = ProgressResponse.model_validate(
            dataclasses.asdict(task.progress),
        )

    return TaskResponse(
        task_id=task.task_id,
        status=task.status.value,
        progress=progress,
        error=task.error,
    )


@ws_router.websocket("/tasks/{task_id}/progress")
async def ws_task_progress(
    task_id: str,
    websocket: WebSocket,
    _auth: None = Depends(require_auth_ws),
) -> None:
    """WebSocket：实时推送任务进度（AGE-12）。"""
    await websocket.accept()

    try:
        manager = _get_manager()
    except HTTPException:
        await websocket.close(code=1011)
        return

    task = manager.get_task(task_id)
    if task is None:
        await websocket.close(code=1008)
        return

    q = await manager.subscribe_progress(task_id)
    if q is None:
        await websocket.close(code=1008)
        return

    try:
        initial = task.progress or TaskProgress(
            stage="ocr",
            message="等待开始",
            message_key="progress.waiting",
        )
        await websocket.send_json(dataclasses.asdict(initial))

        if task.status.value in ("completed", "failed"):
            await websocket.close()
            return

        while True:
            progress = await q.get()
            await websocket.send_json(dataclasses.asdict(progress))

            current_task = manager.get_task(task_id)
            if (
                current_task is not None
                and current_task.status.value in ("completed", "failed")
            ):
                await websocket.close()
                return
    except WebSocketDisconnect:
        return
    finally:
        with suppress(Exception):
            await manager.unsubscribe_progress(task_id, q)


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> TaskListResponse:
    """分页查询任务列表"""
    manager = _get_manager()

    # 限制 page_size 范围
    page_size = max(1, min(page_size, _PAGE_SIZE_MAX))
    page = max(1, page)

    result = await manager.list_tasks(
        status=status, page=page, page_size=page_size,
    )
    return TaskListResponse(
        tasks=[
            TaskListItem.model_validate(dataclasses.asdict(t))
            for t in result.tasks
        ],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.post("/crop/detect", response_model=CropDetectResponse)
async def detect_crop_boxes(req: CropDetectRequest) -> CropDetectResponse:
    """检测 image_dir 下每张图的建议正文裁剪框（供前端"裁剪预览 + 微调"）。

    box=None 表示该图无需裁剪（已裁剪 / 无侧栏 / 检测失败），前端可不画框。
    """
    image_dir = Path(req.image_dir)
    if not await asyncio.to_thread(image_dir.is_dir):
        raise ApiBusinessError(
            APIErrorCode.IMAGE_NOT_FOUND, 404, "图片目录不存在",
        )
    raw = await asyncio.to_thread(detect_boxes_for_dir, image_dir)
    return CropDetectResponse(
        images=[
            CropDetectItem(
                name=name,
                width=w,
                height=h,
                box=(
                    None
                    if box is None
                    else CropBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3])
                ),
            )
            for name, w, h, box in raw
        ],
    )


def _resolve_crop_image(image_dir: str, name: str) -> Path | None:
    """解析 image_dir + 相对名为安全路径；越界 / 非文件返回 None。"""
    root = Path(image_dir).resolve()
    target = (root / name).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


@router.get("/crop/image")
async def get_crop_image(image_dir: str, name: str) -> FileResponse:
    """从 image_dir 按相对名取一张图（供前端裁剪预览显示）。带路径穿越防护。"""
    target = await asyncio.to_thread(_resolve_crop_image, image_dir, name)
    if target is None:
        raise ApiBusinessError(
            APIErrorCode.IMAGE_NOT_FOUND, 404, "图片不存在",
        )
    return FileResponse(target)


def _validate_doc_dir(doc_dir: str | None) -> str | None:
    """校验多文档子目录：返回安全相对路径（空串=根 output_dir）；非法返回 None。"""
    if not doc_dir:
        return ""
    p = PurePosixPath(doc_dir)
    if p.is_absolute() or ".." in p.parts or "." in p.parts:
        return None
    return doc_dir


def _crop_figure_sync(
    src_root: Path,
    filename: str,
    out_images: Path,
    box: CropBox | None,
    quad: CropQuad | None,
) -> str:
    """解析源图（词法包含 + 跟随 symlink）并按 quad/box 裁剪，供线程池调用。

    quad 优先（四角透视校正），否则 box（矩形裁剪）。源图不存在 / 越界 →
    ``FileNotFoundError``；裁剪层的异常（``ValueError`` / ``DegenerateQuadError``
    / ``OSError``）原样上抛由路由分类。两者皆 None 不可达（路由已校验）。
    """
    img_dir = src_root.resolve()
    source = img_dir / filename
    if (
        not source.is_relative_to(img_dir)
        or not source.is_file()
        or source.suffix.lower() not in _IMAGE_EXTS
    ):
        raise FileNotFoundError(filename)
    if quad is not None:
        pts = (
            (quad.tl.x, quad.tl.y),
            (quad.tr.x, quad.tr.y),
            (quad.br.x, quad.br.y),
            (quad.bl.x, quad.bl.y),
        )
        return crop_quad_to_images(source, out_images, pts)
    if box is not None:
        return crop_region_to_images(
            source, out_images, (box.x0, box.y0, box.x1, box.y1),
        )
    raise ValueError("缺少裁剪区域")  # 不可达：路由已校验 box/quad 至少一个


@router.post(
    "/tasks/{task_id}/crop-figure",
    response_model=CropFigureResponse,
)
async def crop_figure(
    task_id: str,
    req: CropFigureRequest,
) -> CropFigureResponse:
    """编辑模式手动重截插图：从某张源图按框裁一块存进文档 images/，返回引用路径。

    供用户在编辑器里看到被切碎 / 缺失的插图时，自己框选源图重新截一张完整图插入。
    裁剪图落 ``output_dir/{doc_dir}/images/manual_N.jpg``，返回 markdown 相对引用
    ``images/manual_N.jpg``（多文档的 doc_dir 前缀由前端 asset URL 重写时补）。
    """
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    filename = req.source_filename
    if not filename or ".." in filename or filename.startswith("/"):
        raise ApiBusinessError(
            APIErrorCode.INVALID_FILENAME, 400, "非法文件名",
        )
    doc_rel = _validate_doc_dir(req.doc_dir)
    if doc_rel is None:
        raise ApiBusinessError(
            APIErrorCode.INVALID_FILENAME, 400, "非法文档目录",
        )
    # box / quad 二选一：quad 优先（四角透视校正），否则 box（矩形裁剪）。
    if req.quad is None and req.box is None:
        raise ApiBusinessError(
            APIErrorCode.INVALID_CROP_REGION, 400, "缺少裁剪区域（box 或 quad）",
        )
    # 绑定为局部变量：闭包内 mypy 不沿用外层的 task 非空 / doc_rel 非 None 窄化。
    src_root = Path(task.image_dir)
    out_images = Path(task.output_dir) / doc_rel / "images"

    try:
        name = await asyncio.to_thread(
            _crop_figure_sync, src_root, filename, out_images,
            req.box, req.quad,
        )
    except DegenerateQuadError as exc:
        raise ApiBusinessError(
            APIErrorCode.INVALID_CROP_REGION, 400, "四角区域退化，无法矫正",
            params={"reason": str(exc)},
        ) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise ApiBusinessError(
            APIErrorCode.IMAGE_NOT_FOUND, 404, "源图不存在或无法读取",
        ) from exc
    except OSError as exc:
        raise ApiBusinessError(
            APIErrorCode.WRITE_FAILED, 500, "裁剪图写盘失败",
            params={"reason": str(exc)},
        ) from exc

    return CropFigureResponse(asset_path=f"images/{name}")


def _requested_crop_boxes(
    req: CreateTaskRequest,
) -> dict[str, tuple[int, int, int, int]]:
    """请求中的手动裁剪框（剔除被任务排除的图）。

    框走任务级 ``OCRConfig.crop_boxes``：OCR 前由 pipeline 裁到任务输出目录，
    **绝不写用户目录**——旧版"建任务前就地覆盖原图"在只读挂载（NAS）上
    cv2.imwrite 静默失败致框默默丢失，可写时又会毁原图，已废弃。
    """
    if not req.crop_boxes:
        return {}
    excluded = (
        set(req.ocr.exclude_images)
        if req.ocr is not None and req.ocr.exclude_images is not None
        else set()
    )
    return {
        name: (b.x0, b.y0, b.x1, b.y1)
        for name, b in req.crop_boxes.items()
        if name not in excluded
    }


#: OCR 请求级覆盖 allowlist：**只**放行这些业务级安全字段，其余一律拒——
#: 含将来误加进 ``OCRConfigRequest`` 的基础设施字段。基础设施字段＝解释器/
#: worker 脚本（可控即 RCE）+ server host/url/model_path（可控即 SSRF /
#: 任意权重加载），只由服务端配置注入（#32 / #33）。用 allowlist 而非
#: denylist：默认拒绝、对 schema 漂移免疫，无需逐一枚举危险字段。新增安全
#: 字段须同步登记到这里，否则其请求级覆盖被静默丢弃（与 schemas.py 的
#: ``OCRConfigRequest`` 字段保持同步）。
_OCR_SAFE_OVERRIDE_ALLOW = frozenset({
    "model",
    "gpu_id",
    "exclude_images",
    "paddle_pipeline",
    "paddle_ocr_timeout",
})


def _resolve_ocr_config(
    req: CreateTaskRequest,
    default_ocr: OCRConfig,
) -> OCRConfig | None:
    """合成任务 OCR 配置：请求级覆盖 + 手动裁剪框（任务级生效）。

    手动框随 OCR 配置入 DB 持久化，resume 自动沿用。只有
    ``_OCR_SAFE_OVERRIDE_ALLOW`` 内的业务字段可被请求覆盖；基础设施字段
    （解释器 / worker 脚本 / 推理服务地址）不在 allowlist 内一律丢弃，
    绝不被请求覆盖（#32 / #33）。
    """
    ocr_cfg: OCRConfig | None = None
    if req.ocr is not None:
        override = {
            key: value
            for key, value in req.ocr.model_dump(exclude_none=True).items()
            if key in _OCR_SAFE_OVERRIDE_ALLOW
        }
        if override:
            ocr_cfg = default_ocr.model_copy(update=override)
    crop_boxes = _requested_crop_boxes(req)
    if crop_boxes:
        ocr_cfg = (ocr_cfg or default_ocr).model_copy(
            update={"crop_boxes": crop_boxes},
        )
    return ocr_cfg


def _resolve_output_dir(req: CreateTaskRequest) -> str | None:
    """归一并校验请求级 ``output_dir``（#34 边界守卫）。

    空串 / 纯空白 → ``None``：交给 ``create_task`` 生成服务端安全默认
    ``{tempdir}/docrestore_{id}``。用户显式指定的必须落在受信工作根下，越界即抛
    ``ApiBusinessError(OUTPUT_DIR_REJECTED, 400)`` fail-fast 不建任务——否则该目录
    会在任务删除时被 ``rmtree`` 递归删除。这里只做"准入校验"，透传去空白后的原始
    路径串（不改写路径形态，删除 sink 会再二次解析校验兜底 TOCTOU）。
    """
    output_dir = (req.output_dir or "").strip() or None
    if output_dir is None:
        return None
    try:
        validate_output_dir(output_dir)
    except OutputDirRejected as exc:
        raise ApiBusinessError(
            APIErrorCode.OUTPUT_DIR_REJECTED, 400,
            f"输出目录被安全策略拒绝：{exc}",
            params={"reason": str(exc)},
        ) from exc
    return output_dir


async def _revalidate_reused_api_base(llm: LLMConfig | None) -> None:
    """resume/retry 复用持久化 ``llm.api_base`` 时重过 SSRF 守卫（#62，关联 #33）。

    ``api_base`` 会持久化（不像 api_key 被排除）；历史失败任务可能存了建于守卫
    之前 / 白名单收紧之前的内网 / 云元数据地址，续跑/重试若不重校验等于绕过 #33。
    DNS 解析阻塞，包进 ``to_thread`` 避免卡事件循环。
    """
    if llm is not None and llm.api_base:
        await asyncio.to_thread(validate_outbound_api_base, llm.api_base)


def _guard_ner_backend(pii_cfg: PIIConfig) -> None:
    """请求级 fail-fast：开实体脱敏但本地 NER 不可用 → 400（名字不裸送云端）。

    仅当 ``enable`` + (人名|机构名) + ``ner_backend="spacy"`` + spaCy 或配置模型不可用
    时拒绝。响应 ``params.remediable=true`` + ``missing_models``，前端据此弹一键配置
    入口（POST /ner/setup，S3.4b）。``ner_backend="none"`` 或关实体脱敏均放行（知情
    放弃）。探测不加载模型（``find_spec``，廉价）。详见 pii-local-ner.md §5。
    """
    if not pii_cfg.enable:
        return
    if not (pii_cfg.redact_person_name or pii_cfg.redact_org_name):
        return
    if pii_cfg.ner_backend != "spacy":
        return
    spacy_installed, installed, missing = probe_availability(pii_cfg.ner_models)
    if spacy_installed and installed:
        return
    raise ApiBusinessError(
        APIErrorCode.NER_BACKEND_UNAVAILABLE, 400,
        "本地 NER 未就绪：开启了人名/机构名脱敏但 spaCy 或模型未安装。请一键配置"
        "本地 NER 环境，或关闭人名/机构名脱敏 / 将 ner_backend 设为 none。",
        params={
            "remediable": True,
            "spacy_installed": spacy_installed,
            "missing_models": missing,
            "models": list(pii_cfg.ner_models),
        },
    )


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    req: CreateTaskRequest,
) -> TaskResponse:
    """创建任务，后台执行 Pipeline。

    本路由是 API 增量字段 → 完整 Config 的唯一合成点：对每类 Config
    取 pipeline 的默认值，`model_copy(update=...)` 叠加请求中的非空字段，
    然后把完整 Config 往下游传（TaskManager / DB / Pipeline 不再做合并）。
    """
    logger.info("收到创建任务请求: image_dir=%s", req.image_dir)
    manager = _get_manager()
    defaults = manager.pipeline.config

    llm_cfg: LLMConfig | None = None
    if req.llm is not None:
        # 请求级 api_base 先过 SSRF 守卫：私网/链路本地/内网/非白名单拒，
        # 环回放行（本地 LLM）。失败 400 fail-fast 不建任务，
        # DNS 解析包进 to_thread（#33）。
        if req.llm.api_base:
            await asyncio.to_thread(
                validate_outbound_api_base, req.llm.api_base,
            )
        llm_cfg = defaults.llm.model_copy(
            update=req.llm.model_dump(exclude_none=True),
        )

    # OCR 覆盖 + 手动裁剪框（任务级，见 _requested_crop_boxes 注释）
    ocr_cfg = _resolve_ocr_config(req, defaults.ocr)

    pii_cfg: PIIConfig | None = None
    if req.pii is not None:
        pii_update: dict[str, object] = {}
        if req.pii.enable is not None:
            pii_update["enable"] = req.pii.enable
        if req.pii.custom_sensitive_words is not None:
            pii_update["custom_sensitive_words"] = (
                _to_custom_words(req.pii.custom_sensitive_words)
            )
        pii_cfg = defaults.pii.model_copy(update=pii_update)

    # 本地 NER fail-fast（S3）：开人名/机构名脱敏但 spaCy/模型未就绪 → 400 不建任务，
    # 避免名字裸送云端或白跑 OCR。校验"有效"配置（请求级覆盖后；ner_* 仍走 defaults）。
    _guard_ner_backend(pii_cfg if pii_cfg is not None else defaults.pii)

    code_cfg: CodeRestoreConfig | None = None
    if req.code is not None:
        code_cfg = defaults.code.model_copy(
            update=req.code.model_dump(exclude_none=True),
        )

    ppt_cfg: PowerPointRestoreConfig | None = None
    if req.ppt is not None:
        ppt_cfg = defaults.ppt.model_copy(
            update=req.ppt.model_dump(exclude_none=True),
        )

    # 文档 / 代码 / PPT 三模式互斥：code 与 ppt 不能同时启用
    if (
        code_cfg is not None and code_cfg.enable
        and ppt_cfg is not None and ppt_cfg.enable
    ):
        raise ApiBusinessError(
            APIErrorCode.MODE_CONFLICT, 400,
            "代码模式与 PPT 模式不能同时启用",
        )

    # output_dir 边界守卫（#34）：越界 400 不建任务，否则该目录会在任务删除时
    # 被 rmtree（任意目录递归删除）。详见 _resolve_output_dir。
    output_dir = _resolve_output_dir(req)

    task = manager.create_task(
        image_dir=req.image_dir,
        output_dir=output_dir,
        llm=llm_cfg,
        ocr=ocr_cfg,
        pii=pii_cfg,
        code=code_cfg,
        ppt=ppt_cfg,
    )
    logger.info("任务已创建: task_id=%s", task.task_id)
    bg = asyncio.create_task(
        manager.run_task(task.task_id),
        name=f"run-task-{task.task_id}",
    )
    try:
        manager.register_running_task(task.task_id, bg)
    except BaseException:
        # register_running_task 抛出（极少见，例如 dict 被外部篡改）时
        # 必须 cancel bg，否则 create_task 启动的协程完全脱管
        bg.cancel()
        raise
    logger.info("后台任务已启动，准备返回响应")
    return TaskResponse(
        task_id=task.task_id,
        status=task.status.value,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    """查询任务状态和进度（含父子任务信息）"""
    return _build_task_response(task_id)


@router.get("/tasks/{task_id}/result")
async def get_result(
    task_id: str,
) -> TaskResultResponse:
    """获取已完成任务的结果。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    if task.status.value != "completed":
        raise ApiBusinessError(
            APIErrorCode.TASK_RESULT_NOT_READY, 404, "任务尚未完成或已失败",
        )

    if task.result is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_RESULT_NOT_READY, 404, "任务尚未完成或已失败",
        )

    return TaskResultResponse(
        task_id=task.task_id,
        output_path=str(task.result.output_path),
        markdown=task.result.markdown,
        doc_title=task.result.doc_title,
        doc_dir=task.result.doc_dir,
    )


@router.get("/tasks/{task_id}/results")
async def get_results(
    task_id: str,
) -> TaskResultsResponse:
    """获取任务已有的全部文档结果列表。

    放宽规则（2026-04-21）：completed 和 failed 都返回，只要 results 非空。
    failed 任务里每个成功子文档可正常预览；失败子文档的 `error` 非空，
    前端据此切换展示（错误文本 vs markdown）。
    """
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    if not task.results:
        raise ApiBusinessError(
            APIErrorCode.TASK_NO_RESULTS, 404,
            "任务尚无结果（未完成或根级错误）",
        )

    items = [
        TaskResultResponse(
            task_id=task.task_id,
            output_path=str(r.output_path),
            markdown=r.markdown,
            doc_title=r.doc_title,
            doc_dir=r.doc_dir,
            error=r.error,
        )
        for r in task.results
    ]
    return TaskResultsResponse(
        task_id=task.task_id,
        results=items,
    )


@router.put(
    "/tasks/{task_id}/results/{result_index}",
    response_model=ActionResponse,
)
async def update_result_markdown(
    task_id: str,
    result_index: int,
    req: UpdateMarkdownRequest,
) -> ActionResponse:
    """更新指定文档的 Markdown 内容（人工精修）。"""
    manager = _get_manager()
    error = await manager.update_result_markdown(
        task_id, result_index, req.markdown,
    )
    if error is not None:
        raise ApiBusinessError(
            APIErrorCode.MARKDOWN_UPDATE_FAILED, 400, error,
            params={"reason": error},
        )

    return ActionResponse(task_id=task_id, message="保存成功")


@router.get("/tasks/{task_id}/assets/{asset_path:path}")
async def get_task_asset(task_id: str, asset_path: str) -> FileResponse:
    """受限访问任务输出资源（AGE-13）。

    支持父任务和子任务。子任务的 output_dir 指向聚类组目录。
    """
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    rel = _validate_asset_path(asset_path)
    if rel is None:
        raise ApiBusinessError(
            APIErrorCode.ASSET_NOT_FOUND, 404, "资源不存在",
        )

    target = _resolve_asset_path(Path(task.output_dir), rel)
    if target is None or not target.exists() or not target.is_file():
        raise ApiBusinessError(
            APIErrorCode.ASSET_NOT_FOUND, 404, "资源不存在",
        )

    return FileResponse(path=target)


@router.get("/tasks/{task_id}/files-index")
async def get_task_files_index(
    task_id: str,
) -> list[dict[str, object]]:
    """返回 AGE-8 代码模式的 ``files-index.json``。

    没跑代码模式 / 任务未完成 / 索引不存在 → 404。
    """
    import json as _json

    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    output_dir = Path(task.output_dir)
    # 多文档场景下 doc_dir 也可能含 files-index.json，简单起见只看根目录
    index_path = output_dir / "files-index.json"
    if not index_path.is_file():
        raise ApiBusinessError(
            APIErrorCode.FILES_INDEX_NOT_FOUND, 404,
            "任务未生成代码索引（非代码模式或未完成）",
        )

    try:
        data = _json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as exc:
        raise ApiBusinessError(
            APIErrorCode.FILES_INDEX_PARSE_ERROR, 500,
            f"索引解析失败: {exc}",
            params={"reason": str(exc)},
        ) from exc

    if not isinstance(data, list):
        raise ApiBusinessError(
            APIErrorCode.FILES_INDEX_BAD_FORMAT, 500, "索引格式异常（非数组）",
        )

    return data


@router.get("/tasks/{task_id}/files/{file_path:path}")
async def get_task_code_file(
    task_id: str,
    file_path: str,
) -> Response:
    """返回 AGE-8 代码模式渲染的源文件文本内容。

    路径限定在 ``output_dir/files/`` 下；任何 ``..`` / 绝对路径 / 非法
    字符 → 404，避免任意文件读取。
    """
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    rel = _validate_code_file_path(file_path)
    if rel is None:
        raise ApiBusinessError(
            APIErrorCode.FILE_NOT_FOUND, 404, "文件不存在",
        )

    output_dir = Path(task.output_dir)
    files_root = (output_dir / "files").resolve(strict=False)
    if not files_root.is_dir():
        raise ApiBusinessError(
            APIErrorCode.CODE_DIR_NOT_FOUND, 404, "代码目录不存在",
        )

    target = (files_root / Path(*rel.parts)).resolve(strict=False)
    if not target.is_relative_to(files_root):
        raise ApiBusinessError(
            APIErrorCode.FILE_NOT_FOUND, 404, "文件不存在",
        )
    if not target.is_file():
        raise ApiBusinessError(
            APIErrorCode.FILE_NOT_FOUND, 404, "文件不存在",
        )

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ApiBusinessError(
            APIErrorCode.READ_FAILED, 500,
            f"读取失败: {exc}",
            params={"reason": str(exc)},
        ) from exc

    return Response(content=content, media_type="text/plain; charset=utf-8")


@router.post(
    "/tasks/{task_id}/code-diagnostics",
    response_model=CodeDiagnosticResponse,
)
async def diagnose_task_code_file(
    task_id: str,
    req: DiagnoseCodeFileRequest,
) -> CodeDiagnosticResponse:
    """对代码模式源文件草稿做只读实时诊断。"""
    from docrestore.processing.code_diagnostics import diagnose_text

    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    rel = _validate_code_file_path(req.file_path)
    if rel is None:
        raise ApiBusinessError(
            APIErrorCode.FILE_NOT_FOUND, 404, "文件不存在",
        )

    output_dir = Path(task.output_dir)
    files_root = (output_dir / "files").resolve(strict=False)
    target = (files_root / Path(*rel.parts)).resolve(strict=False)
    if (
        not files_root.is_dir()
        or not target.is_relative_to(files_root)
        or not target.is_file()
    ):
        raise ApiBusinessError(
            APIErrorCode.FILE_NOT_FOUND, 404, "文件不存在",
        )

    language = _code_file_language_from_index(output_dir, rel)
    diagnostic = await asyncio.to_thread(
        diagnose_text,
        path=rel.as_posix(),
        language=language,
        text=req.content,
        include_root=files_root,
        # 草稿在隔离临时目录诊断，传入真实兄弟目录让同目录 #include 可解析，
        # 避免对依赖同目录头文件的 C/C++ 草稿误报缺失依赖（B7 C19）。
        extra_include_roots=[target.parent],
    )
    return CodeDiagnosticResponse.model_validate(diagnostic.to_index_dict())


# 串行化代码文件保存：write_text + files-index 的 read-modify-write 必须互斥，
# 否则并发 PUT 会相互覆盖、丢失另一文件的 line_count 更新（B7 PUT 竞态）。
_CODE_FILE_WRITE_LOCK = asyncio.Lock()


@router.put(
    "/tasks/{task_id}/files/{file_path:path}",
    response_model=ActionResponse,
)
async def update_task_code_file(
    task_id: str,
    file_path: str,
    req: UpdateCodeFileRequest,
) -> ActionResponse:
    """保存 AGE-8 代码模式渲染的单个源文件。

    只允许写入已存在的 ``output_dir/files/`` 子文件，禁止通过新建路径扩大
    可写范围；保存后同步更新 ``files-index.json`` 的行数摘要。
    """
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    rel = _validate_code_file_path(file_path)
    if rel is None:
        raise ApiBusinessError(
            APIErrorCode.FILE_NOT_FOUND, 404, "文件不存在",
        )

    output_dir = Path(task.output_dir)
    files_root = (output_dir / "files").resolve(strict=False)
    if not files_root.is_dir():
        raise ApiBusinessError(
            APIErrorCode.CODE_DIR_NOT_FOUND, 404, "代码目录不存在",
        )

    target = (files_root / Path(*rel.parts)).resolve(strict=False)
    if not target.is_relative_to(files_root) or not target.is_file():
        raise ApiBusinessError(
            APIErrorCode.FILE_NOT_FOUND, 404, "文件不存在",
        )

    try:
        # 阻塞文件 IO + 索引 RMW 放到线程里跑，并用锁串行化避免并发覆盖（B7 PUT）。
        async with _CODE_FILE_WRITE_LOCK:
            await asyncio.to_thread(
                _write_code_file_and_index,
                target, output_dir, rel, req.content,
            )
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiBusinessError(
            APIErrorCode.WRITE_FAILED, 500,
            f"保存失败: {exc}",
            params={"reason": str(exc)},
        ) from exc

    return ActionResponse(task_id=task_id, message="代码文件已保存")


def _validate_code_file_path(file_path: str) -> PurePosixPath | None:
    """校验 ``files/`` 下的相对路径，禁止 .. / 绝对路径 / 隐藏目录。"""
    if not file_path:
        return None
    p = PurePosixPath(file_path)
    if p.is_absolute() or ".." in p.parts or "." in p.parts:
        return None
    if any(seg.startswith(".") for seg in p.parts):
        return None
    return p


def _count_code_lines(content: str) -> int:
    """按前端编辑器的 ``\\n`` 语义统计显示行数。"""
    return 0 if content == "" else content.count("\n") + 1


def _write_code_file_and_index(
    target: Path,
    output_dir: Path,
    rel: PurePosixPath,
    content: str,
) -> None:
    """同步写入代码文件并刷新 files-index（在线程内执行，由调用方持锁串行化）。"""
    target.write_text(content, encoding="utf-8")
    _update_code_index_after_write(output_dir, rel, content)


def _update_code_index_after_write(
    output_dir: Path,
    rel: PurePosixPath,
    content: str,
) -> None:
    """保存代码文件后同步刷新 files-index 中的基础行数信息。"""
    index_path = output_dir / "files-index.json"
    if not index_path.is_file():
        return

    data: object = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return

    rel_path = rel.as_posix()
    line_count = _count_code_lines(content)
    changed = False
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("path") != rel_path:
            continue
        item["line_count"] = line_count
        raw_range = item.get("line_no_range")
        if (
            isinstance(raw_range, list)
            and len(raw_range) >= 1
            and isinstance(raw_range[0], int)
        ):
            start = raw_range[0]
            item["line_no_range"] = (
                [start, start + line_count - 1] if line_count > 0 else []
            )
        changed = True
        break

    if changed:
        index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _code_file_language_from_index(
    output_dir: Path,
    rel: PurePosixPath,
) -> str | None:
    """从 files-index 读取代码语言，索引异常时交给诊断器按扩展名推断。"""
    index_path = output_dir / "files-index.json"
    if not index_path.is_file():
        return None
    try:
        data: object = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None

    rel_path = rel.as_posix()
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("path") != rel_path:
            continue
        language = item.get("language")
        return language if isinstance(language, str) and language else None
    return None


@router.get("/tasks/{task_id}/download")
async def download_task_result(task_id: str) -> Response:
    """下载任务结果 zip（AGE-13）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    output_dir = Path(task.output_dir)

    # 收集子目录列表；跳过失败的子文档（markdown 未落盘，没什么可下载的）
    doc_dirs = (
        [r.doc_dir for r in task.results if not r.error]
        if task.results
        else []
    )

    # 至少有一个 document.md 存在才能下载
    has_any = any(
        (output_dir / d / "document.md" if d else output_dir / "document.md").exists()
        for d in (doc_dirs or [""])
    )
    if not has_any:
        raise ApiBusinessError(
            APIErrorCode.TASK_RESULT_NOT_READY, 404, "任务尚未完成或已失败",
        )

    zip_bytes = _build_result_zip_bytes(output_dir, doc_dirs)
    filename = f"docrestore_{task_id}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


# ── 源图片访问 ──────────────────────────────────────────

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"})


@router.get("/tasks/{task_id}/quality")
async def get_task_quality_report(task_id: str) -> dict[str, object]:
    """返回任务级质量报告（.quality_report.json）。

    process_tree 多子目录时合并所有子目录的报告；单目录直接返回。
    没有报告（老任务或任务失败前）返回空 issues + 空 summary。
    """
    import asyncio
    import json as _json

    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    def _load() -> dict[str, object]:
        output_root = Path(task.output_dir)
        if not output_root.is_dir():
            return {"summary": {"total": 0}, "issues": []}

        # 收集所有子目录的 .quality_report.json
        reports: list[dict[str, object]] = []
        for p in output_root.rglob(".quality_report.json"):
            try:
                reports.append(_json.loads(
                    p.read_text(encoding="utf-8"),
                ))
            except (OSError, _json.JSONDecodeError):
                continue

        if not reports:
            return {"summary": {"total": 0}, "issues": []}
        if len(reports) == 1:
            return reports[0]

        # 多子目录：合并 issues 列表，聚合 summary 计数
        merged_issues: list[dict[str, object]] = []
        for r in reports:
            issues = r.get("issues", [])
            if isinstance(issues, list):
                merged_issues.extend(issues)
        return {
            "summary": {"total": len(merged_issues)},
            "issues": merged_issues,
        }

    return await asyncio.to_thread(_load)


@router.get(
    "/tasks/{task_id}/source-images",
    response_model=SourceImagesResponse,
)
async def list_source_images(task_id: str) -> SourceImagesResponse:
    """列出任务的源图片文件名（按文件名排序）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    import asyncio

    def _scan() -> list[str]:
        img_dir = Path(task.image_dir)
        if not img_dir.is_dir():
            return []
        # 递归扫描，返回相对于 image_dir 的路径
        return sorted(
            p.relative_to(img_dir).as_posix()
            for p in img_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )

    images = await asyncio.to_thread(_scan)
    return SourceImagesResponse(task_id=task_id, images=images)


@router.get("/tasks/{task_id}/source-images/{filename:path}")
async def get_source_image(task_id: str, filename: str) -> FileResponse:
    """提供单张源图片文件（含路径穿越防护）。"""
    import asyncio

    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    # 路径安全校验
    if not filename or ".." in filename or filename.startswith("/"):
        raise ApiBusinessError(
            APIErrorCode.INVALID_FILENAME, 400, "非法文件名",
        )

    def _resolve() -> Path | None:
        """同步解析并校验图片路径。

        关键（issue #22）：不对拼接路径做 resolve()。服务端源任务的
        image_dir 是 stage 目录，其中的源图是 `_stage_files` 用 `symlink_to`
        指向外部真实文件的软链；resolve 跟随软链后落到 image_dir 之外 →
        包含校验 False → 整个服务端源图预览 404。改为对【未跟随 symlink】
        的词法拼接路径做越界校验（filename 上方已禁止 `..` 与前导 `/`），
        再用 `is_file()` 跟随软链确认目标存在。stage 内软链均由本服务创建、
        指向用户显式选定的图片，放行其目标是安全的。
        """
        img_dir = Path(task.image_dir).resolve()
        candidate = img_dir / filename
        # 词法包含校验：pathlib `is_relative_to` 不访问 FS、不跟随 symlink
        if not candidate.is_relative_to(img_dir):
            return None
        if not candidate.is_file():  # 跟随 symlink 判断目标是否存在
            return None
        if candidate.suffix.lower() not in _IMAGE_EXTS:
            return None
        return candidate

    target = await asyncio.to_thread(_resolve)

    if target is None:
        raise ApiBusinessError(
            APIErrorCode.IMAGE_NOT_FOUND, 404, "图片不存在",
        )

    return FileResponse(path=target)


# ── 任务管理操作 ──────────────────────────────────────


@router.post("/tasks/{task_id}/cancel", response_model=ActionResponse)
async def cancel_task(task_id: str) -> ActionResponse:
    """取消运行中的任务"""
    manager = _get_manager()
    result = await manager.cancel_task(task_id)

    if result is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    if result:
        raise ApiBusinessError(
            APIErrorCode.TASK_ACTION_CONFLICT, 409, result,
            params={"reason": result},
        )

    return ActionResponse(
        task_id=task_id,
        message="任务已取消",
    )


@router.delete("/tasks/{task_id}", response_model=ActionResponse)
async def delete_task(task_id: str) -> ActionResponse:
    """删除任务及其产物"""
    manager = _get_manager()
    result = await manager.delete_task(task_id)

    if result is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    if result:
        raise ApiBusinessError(
            APIErrorCode.TASK_ACTION_CONFLICT, 409, result,
            params={"reason": result},
        )

    return ActionResponse(
        task_id=task_id,
        message="任务及产物已删除",
    )


@router.post("/tasks/cleanup", response_model=TaskCleanupResponse)
async def cleanup_tasks(req: TaskCleanupRequest) -> TaskCleanupResponse:
    """批量清理指定状态的任务（仅允许 completed / failed）。

    对 100+ 历史任务场景，逐个调用 DELETE /tasks/{id} 会产生大量往返，
    此接口一次性清理并返回汇总结果。
    """
    allowed = {"completed", "failed"}
    invalid = [s for s in req.statuses if s not in allowed]
    if invalid:
        raise ApiBusinessError(
            APIErrorCode.CLEANUP_STATUSES_INVALID, 400,
            f"仅允许清理终态任务（completed / failed），非法状态: {invalid}",
            params={"invalid": invalid},
        )
    if not req.statuses:
        raise ApiBusinessError(
            APIErrorCode.CLEANUP_STATUSES_EMPTY, 400, "statuses 不能为空",
        )

    manager = _get_manager()
    deleted_ids, errors = await manager.cleanup_tasks(req.statuses)
    return TaskCleanupResponse(
        deleted=len(deleted_ids),
        failed=len(errors),
        deleted_ids=deleted_ids,
        errors=[f"{tid}: {msg}" for tid, msg in errors],
    )


@router.post("/tasks/{task_id}/retry", response_model=ActionResponse)
async def retry_task(task_id: str) -> ActionResponse:
    """重试失败的任务（从头跑，不复用 output_dir）"""
    manager = _get_manager()
    task = await manager.get_task_async(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )
    # 复用持久化 llm.api_base 前重过 SSRF 守卫（#62，关联 #33）
    await _revalidate_reused_api_base(task.llm)
    result = await manager.retry_task(task_id)

    if result is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    if isinstance(result, str):
        raise ApiBusinessError(
            APIErrorCode.TASK_ACTION_CONFLICT, 409, result,
            params={"reason": result},
        )

    # result 是新创建的 Task
    bg = asyncio.create_task(
        manager.run_task(result.task_id),
        name=f"run-task-{result.task_id}",
    )
    try:
        manager.register_running_task(result.task_id, bg)
    except BaseException:
        bg.cancel()
        raise

    return ActionResponse(
        task_id=result.task_id,
        message="已创建重试任务",
    )


@router.post("/tasks/{task_id}/resume", response_model=ActionResponse)
async def resume_task(task_id: str) -> ActionResponse:
    """继续失败任务 — 复用原 output_dir，OCR 层自动跳过已完成图。

    仅 FAILED 状态（含用户取消）可继续。返回新建 task 的 task_id。
    """
    manager = _get_manager()
    task = await manager.get_task_async(task_id)
    if task is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )
    # 复用持久化 llm.api_base 前重过 SSRF 守卫（#62，关联 #33）
    await _revalidate_reused_api_base(task.llm)
    result = await manager.resume_task(task_id)

    if result is None:
        raise ApiBusinessError(
            APIErrorCode.TASK_NOT_FOUND, 404, "任务不存在",
        )

    if isinstance(result, str):
        raise ApiBusinessError(
            APIErrorCode.TASK_ACTION_CONFLICT, 409, result,
            params={"reason": result},
        )

    bg = asyncio.create_task(
        manager.run_task(result.task_id),
        name=f"run-task-{result.task_id}",
    )
    try:
        manager.register_running_task(result.task_id, bg)
    except BaseException:
        bg.cancel()
        raise

    return ActionResponse(
        task_id=result.task_id,
        message="已创建续跑任务",
    )


# ── 文件系统浏览 ────────────────────────────────────────


_IMAGE_COUNT_CAP = 9999
_PAGE_SIZE_MAX = 100  # 任务列表分页上限（防止单次拉取过大结果）
_STAGE_FILES_MAX = 5000  # 单次服务器侧文件暂存最大数量（防止滥用/超时）


def _count_top_images(dir_path: Path) -> int | None:
    """浅扫描目录，统计顶层图片文件数；不可读返回 None。

    达到 _IMAGE_COUNT_CAP 后停止并返回该上限值（前端可展示 "9999+"）。
    """
    import os

    count = 0
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                ext = Path(entry.name).suffix.lower()
                if ext in _IMAGE_EXTS:
                    count += 1
                    if count >= _IMAGE_COUNT_CAP:
                        return _IMAGE_COUNT_CAP
    except (PermissionError, OSError):
        return None
    return count


def _build_dir_entry(child: Path, with_files: bool) -> DirEntry | None:
    """将目录项转换为 DirEntry；跳过返回 None。

    with_files=True 时目录条目额外携带 image_count（顶层图片数预览）。
    """
    try:
        if child.is_dir():
            image_count = _count_top_images(child) if with_files else None
            return DirEntry(
                name=child.name, is_dir=True, image_count=image_count,
            )
        if with_files and child.is_file():
            if child.suffix.lower() not in _IMAGE_EXTS:
                return None
            try:
                size: int | None = child.stat().st_size
            except OSError:
                size = None
            return DirEntry(name=child.name, is_dir=False, size_bytes=size)
    except PermissionError:
        return None
    return None


def _scan_dir(p: str, with_files: bool) -> BrowseDirsResponse:
    """同步扫描目录。"""
    target = Path(p).expanduser().resolve()
    if not target.is_dir():
        raise ApiBusinessError(
            APIErrorCode.BROWSE_NOT_DIR, 400,
            f"路径不是目录: {target}",
            params={"path": str(target)},
        )

    try:
        children = sorted(target.iterdir(), key=lambda x: x.name.lower())
    except PermissionError:
        raise ApiBusinessError(  # noqa: B904
            APIErrorCode.BROWSE_PERMISSION_DENIED, 403,
            f"无权限访问: {target}",
            params={"path": str(target)},
        )

    entries: list[DirEntry] = []
    for child in children:
        if child.name.startswith("."):
            continue
        entry = _build_dir_entry(child, with_files)
        if entry is not None:
            entries.append(entry)

    parent = str(target.parent) if target.parent != target else None
    return BrowseDirsResponse(
        path=str(target), parent=parent, entries=entries,
    )


@router.get("/filesystem/dirs", response_model=BrowseDirsResponse)
async def browse_dirs(
    path: str = "~", include_files: bool = False,
) -> BrowseDirsResponse:
    """列出指定路径下的子目录和（可选）文件，供前端来源选择器使用。

    - path 为 "~" 时展开为用户主目录
    - 默认仅列出目录；include_files=True 时额外返回 _IMAGE_EXTS 范围内的文件
    - 不可读的目录/文件跳过（不报错）
    """
    return await asyncio.to_thread(_scan_dir, path, include_files)


# ── 服务器源 stage（将已有文件聚合为 image_dir）──────────
#
# 设计：用户在服务器文件浏览器中多选一批图片后，调用本接口，
# 后端在 tempfile.mkdtemp() 目录中为每个文件创建符号链接，返回
# 临时目录路径作为 image_dir，可直接传给 create_task。
# 文件名冲突时追加数字后缀防止覆盖。


def _resolve_stage_path(raw: str) -> Path:
    """校验单个 stage 路径：绝对、可解析、普通文件、图片扩展名。"""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ApiBusinessError(
            APIErrorCode.STAGE_PATH_NOT_ABSOLUTE, 400,
            f"路径必须为绝对路径: {raw}",
            params={"path": raw},
        )
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ApiBusinessError(  # noqa: B904
            APIErrorCode.STAGE_PATH_UNRESOLVABLE, 400,
            f"路径无法解析: {raw} ({exc})",
            params={"path": raw, "reason": str(exc)},
        )
    if not real.is_file():
        raise ApiBusinessError(
            APIErrorCode.STAGE_PATH_NOT_FILE, 400,
            f"不是普通文件: {real}",
            params={"path": str(real)},
        )
    if real.suffix.lower() not in _IMAGE_EXTS:
        raise ApiBusinessError(
            APIErrorCode.STAGE_PATH_BAD_EXT, 400,
            f"不支持的文件类型: {real}",
            params={"path": str(real)},
        )
    return real


def _allocate_link_name(base: str, used: set[str]) -> str:
    """为 symlink 分配唯一文件名，冲突时追加 _1/_2/... 后缀。"""
    if base not in used:
        return base
    stem, ext = Path(base).stem, Path(base).suffix
    idx = 1
    while True:
        candidate = f"{stem}_{idx}{ext}"
        if candidate not in used:
            return candidate
        idx += 1


def _stage_files(raw_paths: list[str]) -> StageServerSourceResponse:
    """同步执行文件校验 + 符号链接创建。"""
    import shutil
    import tempfile

    resolved = [_resolve_stage_path(raw) for raw in raw_paths]

    stage_dir = Path(tempfile.mkdtemp(prefix="docrestore_src_"))
    used_names: set[str] = set()
    for src in resolved:
        name = _allocate_link_name(src.name, used_names)
        used_names.add(name)
        try:
            (stage_dir / name).symlink_to(src)
        except OSError as exc:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise ApiBusinessError(  # noqa: B904
                APIErrorCode.STAGE_SYMLINK_FAILED, 500,
                f"创建符号链接失败: {src} → {exc}",
                params={"path": str(src), "reason": str(exc)},
            )

    logger.info(
        "服务器源 stage 完成: %d 个文件 → %s",
        len(resolved), stage_dir,
    )
    return StageServerSourceResponse(
        image_dir=str(stage_dir),
        file_count=len(resolved),
    )


@router.post("/sources/server", response_model=StageServerSourceResponse)
async def stage_server_source(
    req: StageServerSourceRequest,
) -> StageServerSourceResponse:
    """将服务器上已有文件 stage 为可作为 image_dir 使用的临时目录。

    - 每个路径必须绝对、存在、为普通文件、扩展名在 _IMAGE_EXTS 内
    - 服务端创建 /tmp/docrestore_src_xxx 目录，为每个文件创建符号链接
    - 返回临时目录路径，调用方使用后自行管理生命周期（不自动清理）
    """
    if not req.paths:
        raise ApiBusinessError(
            APIErrorCode.STAGE_PATHS_EMPTY, 400, "paths 不能为空",
        )
    if len(req.paths) > _STAGE_FILES_MAX:
        raise ApiBusinessError(
            APIErrorCode.STAGE_TOO_MANY_FILES, 400,
            f"单次最多 {_STAGE_FILES_MAX} 个文件",
            params={"max": _STAGE_FILES_MAX},
        )

    return await asyncio.to_thread(_stage_files, req.paths)


# ── OCR 引擎预热 ──────────────────────────────────────────


def _get_engine_manager(request: Request) -> EngineManager:
    """从 app.state 获取 EngineManager 实例。"""
    em: EngineManager | None = getattr(request.app.state, "engine_manager", None)
    if em is None:
        raise ApiBusinessError(
            APIErrorCode.ENGINE_MANAGER_NOT_INITIALIZED, 500,
            "EngineManager 未初始化",
        )
    return em


@router.get("/ocr/status", response_model=OCRStatusResponse)
async def get_ocr_status(request: Request) -> OCRStatusResponse:
    """查询当前 OCR 引擎状态。"""
    em = _get_engine_manager(request)
    return OCRStatusResponse(
        current_model=em.current_model,
        current_gpu=em.current_gpu,
        current_gpu_name=em.current_gpu_name,
        is_ready=em.is_ready,
        is_switching=em.is_switching,
    )


@router.get("/ner/status", response_model=NERStatusResponse)
async def get_ner_status() -> NERStatusResponse:
    """本地 NER 可用性探测（不加载模型）。前端开人名/机构名脱敏时拉取。"""
    pii_cfg = _get_manager().pipeline.config.pii
    spacy_installed, installed, missing = probe_availability(pii_cfg.ner_models)
    return NERStatusResponse(
        available=spacy_installed and bool(installed),
        spacy_installed=spacy_installed,
        configured_models=list(pii_cfg.ner_models),
        installed_models=installed,
        missing_models=missing,
    )


def _get_ner_setup(request: Request) -> NERSetupManager:
    """从 app.state 获取 NERSetupManager（lifespan 注入）。"""
    mgr: NERSetupManager | None = getattr(
        request.app.state, "ner_setup", None,
    )
    if mgr is None:
        raise HTTPException(status_code=500, detail="NER 安装管理器未初始化")
    return mgr


@router.post("/ner/setup", response_model=NERSetupStatusResponse)
async def start_ner_setup(request: Request) -> NERSetupStatusResponse:
    """一键安装本地 NER 环境（spaCy + 模型，装进当前 venv）。

    单任务串行：已有安装在跑 → 409。模型集取服务端配置 ``pii.ner_models``（白名单），
    pip / spacy download 幂等（已装跳过）。进度轮询 ``GET /ner/setup/status``。
    """
    mgr = _get_ner_setup(request)
    pii_cfg = _get_manager().pipeline.config.pii
    try:
        started = await mgr.start(pii_cfg.ner_models)
    except ValueError as exc:
        raise ApiBusinessError(
            APIErrorCode.NER_SETUP_INVALID_MODEL, 400, str(exc),
        ) from exc
    if not started:
        raise ApiBusinessError(
            APIErrorCode.NER_SETUP_IN_PROGRESS, 409,
            "本地 NER 环境安装已在进行中，请等待完成",
        )
    return NERSetupStatusResponse.model_validate(mgr.status())


@router.get("/ner/setup/status", response_model=NERSetupStatusResponse)
async def get_ner_setup_status(request: Request) -> NERSetupStatusResponse:
    """轮询本地 NER 环境安装进度（state / log / error）。"""
    return NERSetupStatusResponse.model_validate(
        _get_ner_setup(request).status(),
    )


@router.get("/gpus", response_model=GPUListResponse)
async def list_available_gpus() -> GPUListResponse:
    """枚举系统可见的 GPU + 推荐索引。

    前端据此渲染"GPU 选择"下拉；"自动"项默认 value="" 交给后端的
    `pick_best_gpu()`。GPU 探测不抢 gpu_lock，允许并发调用。
    """
    gpus = await asyncio.to_thread(list_gpus)
    return GPUListResponse(
        gpus=[
            GPUInfoResponse(
                index=g.index,
                name=g.name,
                memory_total_mb=g.memory_total_mb,
                memory_free_mb=g.memory_free_mb,
                compute_capability=g.compute_capability,
            )
            for g in gpus
        ],
        recommended=pick_best_gpu(gpus),
    )


@router.post("/ocr/warmup")
async def warmup_ocr_engine(
    req: OCRWarmupRequest,
    request: Request,
) -> dict[str, str]:
    """预加载指定 OCR 引擎（后台异步，立即返回）。"""
    em = _get_engine_manager(request)

    # gpu_id=None 先落地成推荐值，便于 is_ready 匹配和日志可读
    target_gpu = req.gpu_id or pick_best_gpu() or "0"

    # 已匹配且就绪 → 直接返回
    if (
        em.is_ready
        and em.current_model == req.model
        and em.current_gpu == target_gpu
    ):
        return {"status": "ready", "message": "引擎已就绪"}

    # 正在切换 → 返回 switching 状态
    if em.is_switching:
        return {"status": "switching", "message": "引擎正在切换中"}

    # 构造完整配置并发起后台预热
    manager = _get_manager()
    warmup_config = manager.pipeline.config.ocr.model_copy(
        update={"model": req.model, "gpu_id": target_gpu},
    )

    async def _do_warmup() -> None:
        """后台执行引擎预热。"""
        try:
            await em.ensure(warmup_config)
            logger.info(
                "OCR 引擎预热完成: %s (GPU %s)",
                req.model, target_gpu,
            )
        except asyncio.CancelledError:
            # 应用 shutdown 时 TaskManager 会 cancel 所有后台任务
            logger.info("OCR 引擎预热被取消")
            raise
        except Exception:
            logger.warning("OCR 引擎预热失败", exc_info=True)

    # 通过 TaskManager 统一追踪，shutdown 时 cancel + gather
    manager.spawn_background(_do_warmup(), name=f"ocr-warmup-{req.model}")
    return {"status": "accepted", "message": "引擎预热已开始"}
