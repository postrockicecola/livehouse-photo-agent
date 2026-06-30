"""Portrait → cartoon via local ComfyUI (方案 B)."""
from __future__ import annotations

import copy
import io
import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

from services.personal.comfy_client import ComfyUIClient, ComfyUIError, load_workflow_template

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_config_path() -> Path:
    raw = (__import__("os").environ.get("LIVEHOUSE_PORTRAIT_CONFIG") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (_REPO_ROOT / p).resolve()
    return _REPO_ROOT / "configs" / "comfy" / "portrait_cartoon.yaml"
_JOBS_ROOT = _REPO_ROOT / ".runtime" / "personal" / "portrait_cartoon"

_lock = threading.Lock()
_active_threads: dict[str, threading.Thread] = {}


@dataclass(frozen=True)
class PortraitCartoonSettings:
    backend: str
    base_url: str
    workflow_path: Path
    timeout_seconds: int
    poll_interval_seconds: float
    checkpoint_name: str
    vae_name: str
    unet_name: str
    clip_l_name: str
    clip_t5_name: str
    nodes: dict[str, str]
    defaults: dict[str, Any]
    negative_prompt: str
    prompt_prefix: str
    prompt_suffix: str
    max_reference_long_edge: int
    style_seed: int | None


def load_settings() -> PortraitCartoonSettings:
    env_url = (__import__("os").environ.get("LIVEHOUSE_COMFY_URL") or "").strip()
    env_wf = (__import__("os").environ.get("LIVEHOUSE_COMFY_PORTRAIT_WORKFLOW") or "").strip()
    config_path = _resolve_config_path()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    wf = Path(env_wf) if env_wf else (_REPO_ROOT / str(raw.get("workflow_path", ""))).resolve()
    nodes = raw.get("nodes") or {}
    return PortraitCartoonSettings(
        backend=str(raw.get("backend", "sd15_img2img")),
        base_url=env_url or str(raw.get("base_url", "http://127.0.0.1:8188")),
        workflow_path=wf,
        timeout_seconds=int(raw.get("timeout_seconds", 600)),
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 2)),
        checkpoint_name=str(raw.get("checkpoint_name", "dreamshaper_8.safetensors")),
        vae_name=str(raw.get("vae_name", "")).strip(),
        unet_name=str(raw.get("unet_name", "")).strip(),
        clip_l_name=str(raw.get("clip_l_name", "")).strip(),
        clip_t5_name=str(raw.get("clip_t5_name", "")).strip(),
        nodes={str(k): str(v) for k, v in nodes.items()},
        defaults=dict(raw.get("defaults") or {}),
        negative_prompt=str(raw.get("negative_prompt", "")),
        prompt_prefix=str(raw.get("prompt_prefix", "")),
        prompt_suffix=str(raw.get("prompt_suffix", "")),
        max_reference_long_edge=int(raw.get("max_reference_long_edge", 768)),
        style_seed=_parse_style_seed(raw.get("style_seed")),
    )


def _parse_style_seed(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value % (2**31)


def _job_dir(job_id: str) -> Path:
    return _JOBS_ROOT / job_id


def _write_meta(job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    d = _job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    meta_path = d / "meta.json"
    base: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            base = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            base = {}
    base.update(patch)
    base["job_id"] = job_id
    base["updated_at"] = int(time.time())
    meta_path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return base


def get_job(job_id: str) -> dict[str, Any] | None:
    meta_path = _job_dir(job_id) / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


_HEALTH_CACHE_TTL_S = 15.0
_health_cache: tuple[float, dict[str, Any]] | None = None


def comfy_health() -> dict[str, Any]:
    global _health_cache
    now = time.time()
    if _health_cache is not None and now - _health_cache[0] < _HEALTH_CACHE_TTL_S:
        return _health_cache[1]

    s = load_settings()
    client = ComfyUIClient(s.base_url, timeout=2.0)
    ok = client.ping()
    wf_ok = s.workflow_path.is_file()
    payload = {
        "backend": s.backend,
        "config_path": str(_resolve_config_path()),
        "comfy_reachable": ok,
        "comfy_url": s.base_url,
        "workflow_configured": wf_ok,
        "workflow_path": str(s.workflow_path),
        "checkpoint_name": s.checkpoint_name,
        "vae_name": s.vae_name or None,
        "unet_name": s.unet_name or None,
        "clip_l_name": s.clip_l_name or None,
        "clip_t5_name": s.clip_t5_name or None,
        "ready": ok and wf_ok,
    }
    _health_cache = (now, payload)
    return payload


_MODE_EXTRA_SUFFIX: dict[str, str] = {
    "likeness": ", same person as reference photo, preserve facial identity, matching face structure",
    "balanced": ", coherent anatomy, clear scene elements from description",
    "scene": ", detailed background matching description, full environment, wide composition",
    "face_only": (
        ", face crop reference only: generate full body and environment from prompt, same person, wide shot"
    ),
}

_MODE_EXTRA_SUFFIX_FLUX: dict[str, str] = {
    "likeness": ", same character identity, keep face similar but stylized as cartoon",
    "balanced": ", follow the described dance move and scene, mid-action pose, full body if described",
    "scene": ", strong scene and action from prompt, full body dynamic dance pose, wide shot, environment clearly visible",
    "face_only": (
        ", reference is face crop only: invent full body, outfit, and complete environment from the prompt, "
        "same person face identity, cartoon illustration, dynamic action pose, wide shot showing the whole scene"
    ),
}

_MODE_DEFAULT_DENOISE: dict[str, float] = {
    "likeness": 0.58,
    "balanced": 0.68,
    "scene": 0.80,
    "face_only": 0.88,
}

# FLUX img2img：latent 来自照片，denoise 偏低会几乎仍是原图（不像卡通、也不会跳舞）
_MODE_DEFAULT_DENOISE_FLUX: dict[str, float] = {
    "likeness": 0.64,
    "balanced": 0.76,
    "scene": 0.84,
    "face_only": 0.9,
}

# InstantID：文生图 + 脸参考，denoise 保持 1.0
_MODE_EXTRA_SUFFIX_INSTANTID: dict[str, str] = _MODE_EXTRA_SUFFIX_FLUX

_MODE_DEFAULT_DENOISE_INSTANTID: dict[str, float] = {
    "likeness": 1.0,
    "balanced": 1.0,
    "scene": 1.0,
    "face_only": 1.0,
}


def _is_instantid_backend(backend: str) -> bool:
    return backend in ("instantid_sdxl", "instantid")


def _is_flux_backend(backend: str) -> bool:
    return backend in ("flux_dev", "flux_fp8_checkpoint")


_VALID_GENERATION_MODES = frozenset({* _MODE_EXTRA_SUFFIX.keys(), "face_only"})


def _normalize_generation_mode(mode: str | None) -> str:
    key = (mode or "balanced").strip().lower()
    return key if key in _VALID_GENERATION_MODES else "balanced"


def _inject_workflow(
    template: dict[str, Any],
    *,
    settings: PortraitCartoonSettings,
    uploaded_name: str,
    user_prompt: str,
    seed: int,
    denoise: float | None,
    generation_mode: str = "balanced",
) -> dict[str, Any]:
    wf = copy.deepcopy(template)
    n = settings.nodes
    pos_id = n.get("positive_prompt", "2")
    neg_id = n.get("negative_prompt", "3")
    load_id = n.get("load_image", "8")
    samp_id = n.get("ksampler", "5")
    ckpt_id = "1"

    mode = _normalize_generation_mode(generation_mode)
    if _is_flux_backend(settings.backend):
        extra = _MODE_EXTRA_SUFFIX_FLUX.get(mode, _MODE_EXTRA_SUFFIX_FLUX["balanced"])
    elif _is_instantid_backend(settings.backend):
        extra = _MODE_EXTRA_SUFFIX_INSTANTID.get(mode, _MODE_EXTRA_SUFFIX_INSTANTID["balanced"])
    else:
        extra = _MODE_EXTRA_SUFFIX.get(mode, _MODE_EXTRA_SUFFIX["balanced"])
    full_positive = f"{settings.prompt_prefix}{user_prompt.strip()}{settings.prompt_suffix}{extra}".strip()

    if ckpt_id in wf and wf[ckpt_id].get("class_type") == "CheckpointLoaderSimple":
        wf[ckpt_id].setdefault("inputs", {})["ckpt_name"] = settings.checkpoint_name

    vae_id = n.get("vae_loader", "")
    if vae_id and settings.vae_name and vae_id in wf:
        if wf[vae_id].get("class_type") == "VAELoader":
            wf[vae_id].setdefault("inputs", {})[n.get("vae_name_input", "vae_name")] = settings.vae_name

    unet_id = n.get("unet_loader", "")
    if unet_id and settings.unet_name and unet_id in wf:
        if wf[unet_id].get("class_type") == "UNETLoader":
            wf[unet_id].setdefault("inputs", {})[n.get("unet_name_input", "unet_name")] = settings.unet_name

    clip_id = n.get("dual_clip_loader", "")
    if clip_id and clip_id in wf and wf[clip_id].get("class_type") == "DualCLIPLoader":
        cinp = wf[clip_id].setdefault("inputs", {})
        if settings.clip_l_name:
            cinp[n.get("clip_l_input", "clip_name1")] = settings.clip_l_name
        if settings.clip_t5_name:
            cinp[n.get("clip_t5_input", "clip_name2")] = settings.clip_t5_name

    if load_id in wf:
        wf[load_id].setdefault("inputs", {})[n.get("load_image_input", "image")] = uploaded_name

    if pos_id in wf:
        wf[pos_id].setdefault("inputs", {})[n.get("positive_prompt_input", "text")] = full_positive

    if neg_id in wf:
        wf[neg_id].setdefault("inputs", {})[n.get("negative_prompt_input", "text")] = settings.negative_prompt

    if samp_id in wf:
        inp = wf[samp_id].setdefault("inputs", {})
        inp[n.get("seed_input", "seed")] = seed
        for key in ("steps", "cfg", "sampler_name", "scheduler"):
            if key in settings.defaults:
                inp[key] = settings.defaults[key]
        if denoise is not None:
            inp[n.get("denoise_input", "denoise")] = denoise
        elif "denoise" in settings.defaults:
            inp[n.get("denoise_input", "denoise")] = settings.defaults["denoise"]

    return wf


def _prepare_reference_image(image_bytes: bytes, max_long_edge: int) -> tuple[bytes, dict[str, Any]]:
    """Downscale large uploads so Mac MPS img2img stays within buffer limits."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im.load()
        orig_w, orig_h = im.size
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")

        w, h = im.size
        long_edge = max(w, h)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        im.save(buf, format="PNG")
        meta = {
            "reference_original_size": [orig_w, orig_h],
            "reference_upload_size": list(im.size),
        }
        return buf.getvalue(), meta


def _format_comfy_error(msgs: Any) -> str:
    raw = str(msgs)
    lower = raw.lower()
    if "invalid buffer size" in lower or "out of memory" in lower:
        return (
            "ComfyUI 内存不足（Mac MPS 常见）。参考图已自动缩小；请降低重绘强度、关闭 Ollama/Celery 后重试。"
            f" 原始错误: {raw[:400]}"
        )
    if "vae is invalid" in lower or "does not contain a valid vae" in lower:
        return (
            "FLUX checkpoint 未内置 VAE。请将 ae.safetensors 放入 ComfyUI/models/vae/，"
            "并确认 portrait_cartoon_flux.yaml 中 vae_name: ae.safetensors。"
            f" 原始错误: {raw[:300]}"
        )
    if "value_not_in_list" in lower and ("clip_name" in lower or "unet_name" in lower):
        return (
            "ComfyUI 在对应目录里找不到模型文件（列表为空）。"
            " flux1-dev → models/diffusion_models/（若在 checkpoints/ 请做软链接）；"
            " clip_l.safetensors 与 t5xxl_fp16.safetensors → models/text_encoders/。"
            " 详见 configs/comfy/GUIDE_FLUX_STEP_BY_STEP.md。"
            f" 原始错误: {raw[:400]}"
        )
    if "instantid" in lower or "insightface" in lower or "antelope" in lower:
        return (
            "InstantID 依赖未就绪。请按 configs/comfy/GUIDE_INSTANTID_STEP_BY_STEP.md 安装 "
            "ComfyUI_InstantID、antelopev2、ip-adapter.bin、ControlNet 与 SDXL checkpoint。"
            f" 原始错误: {raw[:400]}"
        )
    return f"ComfyUI execution error: {msgs}"


_UNSAFE_PROMPT_FRAGMENTS = (
    "nsfw",
    "nude",
    "naked",
    "topless",
    "bottomless",
    "lingerie",
    "underwear",
    "panties",
    "bikini",
    "erotic",
    "porn",
    "pornographic",
    "sexual",
    "lewd",
    "hentai",
    "ecchi",
    "裸体",
    "色情",
    "裸露",
    "性感",
)


def _assert_safe_prompt(user_prompt: str) -> None:
    lower = user_prompt.lower()
    for frag in _UNSAFE_PROMPT_FRAGMENTS:
        if frag in lower:
            raise ValueError("prompt contains disallowed content; keep descriptions SFW (fully clothed dance/scene)")


def _resolve_seed(settings: PortraitCartoonSettings, seed: int | None) -> int:
    if seed is not None:
        return int(seed) % (2**31)
    if settings.style_seed is not None:
        return settings.style_seed
    return random.randint(0, 2**31 - 1)


def _extract_output_image(history: dict[str, Any]) -> tuple[str, str] | None:
    outputs = history.get("outputs") or {}
    for _node_id, node_out in outputs.items():
        images = node_out.get("images") or []
        if not images:
            continue
        first = images[0]
        if isinstance(first, dict) and first.get("filename"):
            return str(first["filename"]), str(first.get("subfolder") or "")
    return None


def _run_job(
    job_id: str,
    user_prompt: str,
    denoise: float | None,
    seed: int,
    generation_mode: str,
) -> None:
    settings = load_settings()
    client = ComfyUIClient(settings.base_url, timeout=float(settings.timeout_seconds))
    job_d = _job_dir(job_id)
    ref_path = job_d / "reference.png"
    try:
        _write_meta(job_id, {"status": "running", "message": "uploading to ComfyUI"})
        up = client.upload_image(ref_path)
        uploaded_name = str(up["name"])

        template = load_workflow_template(settings.workflow_path)
        workflow = _inject_workflow(
            template,
            settings=settings,
            uploaded_name=uploaded_name,
            user_prompt=user_prompt,
            seed=seed,
            denoise=denoise,
            generation_mode=generation_mode,
        )
        _write_meta(
            job_id,
            {"status": "running", "message": "queued in ComfyUI", "seed": seed, "generation_mode": generation_mode},
        )
        prompt_id = client.queue_prompt(workflow)
        _write_meta(job_id, {"comfy_prompt_id": prompt_id})

        deadline = time.time() + settings.timeout_seconds
        while time.time() < deadline:
            hist = client.get_history(prompt_id)
            if hist and hist.get("outputs"):
                picked = _extract_output_image(hist)
                if picked:
                    filename, subfolder = picked
                    out_path = job_d / "output.png"
                    client.download_view(
                        filename=filename,
                        subfolder=subfolder,
                        folder_type="output",
                        dest=out_path,
                    )
                    _write_meta(
                        job_id,
                        {
                            "status": "succeeded",
                            "message": "done",
                            "output_file": "output.png",
                            "comfy_output": {"filename": filename, "subfolder": subfolder},
                        },
                    )
                    return
            st = hist.get("status") if hist else None
            if isinstance(st, dict) and st.get("status_str") == "error":
                msgs = st.get("messages") or []
                raise ComfyUIError(_format_comfy_error(msgs))
            time.sleep(settings.poll_interval_seconds)

        raise ComfyUIError("timed out waiting for ComfyUI result")
    except ComfyUIError as e:
        logger.exception("portrait cartoon job failed", extra={"job_id": job_id})
        _write_meta(job_id, {"status": "failed", "message": str(e), "error": str(e)})
    except Exception as e:
        logger.exception("portrait cartoon job failed", extra={"job_id": job_id})
        _write_meta(job_id, {"status": "failed", "message": str(e), "error": str(e)})
    finally:
        with _lock:
            _active_threads.pop(job_id, None)


def create_job(
    *,
    image_bytes: bytes,
    user_prompt: str,
    denoise: float | None = None,
    seed: int | None = None,
    generation_mode: str | None = None,
) -> dict[str, Any]:
    if not user_prompt.strip():
        raise ValueError("prompt is required")
    _assert_safe_prompt(user_prompt)
    if len(image_bytes) > 15 * 1024 * 1024:
        raise ValueError("image too large (max 15MB)")
    health = comfy_health()
    if not health["ready"]:
        raise ComfyUIError(
            "ComfyUI not ready: start ComfyUI locally and ensure workflow/checkpoint paths in "
            f"{_resolve_config_path()} (or set LIVEHOUSE_PORTRAIT_CONFIG)"
        )

    job_id = uuid.uuid4().hex[:16]
    job_d = _job_dir(job_id)
    job_d.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    mode = _normalize_generation_mode(generation_mode)
    if denoise is None:
        if _is_flux_backend(settings.backend):
            denoise = _MODE_DEFAULT_DENOISE_FLUX[mode]
        elif _is_instantid_backend(settings.backend):
            denoise = _MODE_DEFAULT_DENOISE_INSTANTID[mode]
        else:
            denoise = _MODE_DEFAULT_DENOISE[mode]
    resolved_seed = _resolve_seed(settings, seed)
    ref_bytes, ref_meta = _prepare_reference_image(image_bytes, settings.max_reference_long_edge)
    (job_d / "reference.png").write_bytes(ref_bytes)
    meta = _write_meta(
        job_id,
        {
            "status": "queued",
            "message": "starting",
            "backend": settings.backend,
            "user_prompt": user_prompt.strip(),
            "denoise": denoise,
            "seed": resolved_seed,
            "generation_mode": mode,
            "created_at": int(time.time()),
            **ref_meta,
        },
    )

    def _target() -> None:
        _run_job(job_id, user_prompt, denoise, resolved_seed, mode)

    t = threading.Thread(target=_target, name=f"portrait-cartoon-{job_id}", daemon=True)
    with _lock:
        _active_threads[job_id] = t
    t.start()
    return meta


def output_path(job_id: str) -> Path | None:
    p = _job_dir(job_id) / "output.png"
    return p if p.is_file() else None


def reference_path(job_id: str) -> Path | None:
    p = _job_dir(job_id) / "reference.png"
    return p if p.is_file() else None
