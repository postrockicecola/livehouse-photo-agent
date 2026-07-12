"""FastAPI app bootstrap for gallery routes.

Product entry: mount ``api.gallery_routes`` (user/gallery) and ``api.infra_routes`` (jobs/workers/operators).
Both routers belong to the **current main path**; ``infra_routes`` is the control-plane / observability surface,
not a separate product fork.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from api.agent_routes import router as agent_router
from api.auth_routes import router as auth_router
from api.gallery_routes import configure_gallery_routes, router as gallery_router
from api.infra_routes import router as infra_router
from api.personal_routes import router as personal_router


_REPO_ROOT = Path(__file__).resolve().parent


def _parse_gallery_cli() -> tuple[str, int]:
    argv = sys.argv[1:]
    config_path = _REPO_ROOT / "configs" / "livehouse.yaml"
    positionals = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(
                "用法:\n"
                "  python gallery_server.py [数据目录] [端口]\n"
                "  python gallery_server.py --config path/to/livehouse.yaml\n\n"
                "未指定数据目录时，使用 configs/livehouse.yaml 的 paths.source_dir。\n"
            )
            sys.exit(0)
        if a == "--config" and i + 1 < len(argv):
            raw = Path(argv[i + 1]).expanduser()
            config_path = raw if raw.is_absolute() else (_REPO_ROOT / raw).resolve()
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        positionals.append(a)
        i += 1

    port = 8080
    base_dir: str | None = None
    for p in positionals:
        if base_dir is None:
            cand = Path(p).expanduser().resolve()
            if cand.is_dir():
                base_dir = str(cand)
                continue
        try:
            port = int(p)
        except ValueError:
            pass

    if base_dir is None and config_path.is_file():
        try:
            from utils.config_loader import ConfigLoader

            cfg = ConfigLoader.load(str(config_path))
            sd = cfg.get("paths", {}).get("source_dir")
            if sd:
                base_dir = str(Path(sd).expanduser().resolve())
        except Exception:
            pass

    if base_dir is None:
        base_dir = os.getcwd()

    if not os.path.isdir(base_dir):
        base_dir = os.getcwd()
    return base_dir, port


BASE_DIR, GALLERY_PORT = _parse_gallery_cli()
RESULTS_JSON = os.path.join(BASE_DIR, "analysis_results.json")
configure_gallery_routes(BASE_DIR)

app = FastAPI(title="Livehouse Gallery API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(gallery_router)
app.include_router(infra_router)
app.include_router(personal_router)
app.include_router(agent_router)
app.include_router(auth_router)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(_request: Request, exc: Exception):
    import traceback

    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "success": False, "error": "internal server error"},
    )

_static_dir = _REPO_ROOT / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/healthz")
def healthz():
    from infra.health import health_report
    from utils.config_loader import ConfigLoader
    from utils.luma_brain import brain_connect

    cfg = ConfigLoader.load()
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    provider = str(model_cfg.get("provider", "ollama"))
    endpoint = str(model_cfg.get("endpoint", "http://localhost:11434"))
    conn = brain_connect()
    try:
        report = health_report(conn, provider=provider, endpoint=endpoint)
        return report
    finally:
        conn.close()


@app.get("/readyz")
def readyz():
    from infra.health import db_health

    db = db_health()
    return {"ok": bool(db.get("ok")), "checks": {"db": db}}


@app.get("/metrics")
def metrics():
    from infra.metrics import render_prometheus_metrics

    payload, content_type = render_prometheus_metrics()
    return Response(content=payload, media_type=content_type)


if __name__ == "__main__":
    import uvicorn

    print("🚀 启动 FastAPI Gallery 服务")
    print(f"📊 analysis_results.json: {RESULTS_JSON}")
    lab = os.getenv("LIVEHOUSE_LAB_URL", "http://127.0.0.1:3000").rstrip("/")
    print(f"🌍 Studio（浏览器）: {lab}/")
    print(f"🖼  Gallery: {lab}/gallery")
    print(f"🔗 API / 着陆页: http://127.0.0.1:{GALLERY_PORT}/")
    print(f"📺 流式相册: http://127.0.0.1:{GALLERY_PORT}/static/gallery.html")
    print(f"📁 BASE_DIR: {BASE_DIR}")
    limit_conc = max(8, int(os.getenv("LIVEHOUSE_API_LIMIT_CONCURRENCY", "48")))
    uvicorn.run(
        "gallery_server:app",
        host="0.0.0.0",
        port=GALLERY_PORT,
        reload=False,
        limit_concurrency=limit_conc,
        timeout_keep_alive=5,
    )
