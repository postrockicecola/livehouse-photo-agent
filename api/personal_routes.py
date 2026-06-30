"""Personal edition API (local ComfyUI portrait cartoon, etc.)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from services.personal.comfy_client import ComfyUIError
from services.personal import portrait_cartoon as pc

router = APIRouter(prefix="/api/personal", tags=["personal"])


@router.get("/portrait-cartoon/health")
def portrait_cartoon_health():
    return pc.comfy_health()


@router.post("/portrait-cartoon/jobs")
async def portrait_cartoon_create_job(
    image: UploadFile = File(...),
    prompt: str = Form(...),
    denoise: float | None = Form(default=None),
    seed: int | None = Form(default=None),
    generation_mode: str | None = Form(default=None),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="file must be an image")
    data = await image.read()
    try:
        meta = pc.create_job(
            image_bytes=data,
            user_prompt=prompt,
            denoise=denoise,
            seed=seed,
            generation_mode=generation_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ComfyUIError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {
        "ok": True,
        "job_id": meta["job_id"],
        "status": meta.get("status"),
        "poll_url": f"/api/personal/portrait-cartoon/jobs/{meta['job_id']}",
        "seed": meta.get("seed"),
    }


@router.get("/portrait-cartoon/jobs/{job_id}")
def portrait_cartoon_job_status(job_id: str):
    meta = pc.get_job(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="job not found")
    out: dict = {
        "ok": True,
        "job_id": job_id,
        "status": meta.get("status"),
        "message": meta.get("message"),
        "error": meta.get("error"),
        "seed": meta.get("seed"),
    }
    if meta.get("status") == "succeeded" and pc.output_path(job_id):
        out["output_url"] = f"/api/personal/portrait-cartoon/jobs/{job_id}/output"
    return out


@router.get("/portrait-cartoon/jobs/{job_id}/output")
def portrait_cartoon_job_output(job_id: str):
    path = pc.output_path(job_id)
    if not path:
        raise HTTPException(status_code=404, detail="output not ready")
    return FileResponse(path, media_type="image/png", filename=f"portrait_cartoon_{job_id}.png")


@router.get("/portrait-cartoon/jobs/{job_id}/reference")
def portrait_cartoon_job_reference(job_id: str):
    ref = pc.reference_path(job_id)
    if not ref:
        raise HTTPException(status_code=404, detail="reference not found")
    return FileResponse(ref, media_type="image/png")
