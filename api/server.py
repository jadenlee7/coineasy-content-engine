"""
CoinEasy Content Engine · FastAPI Server

Single service serving multiple clients (Yellow, Squid, etc.) via:
    POST /clients/{client_id}/generate

Authentication: X-API-Key header
Deployment: Railway
"""
import os
import time
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.client_config import list_active_clients, load_client_config, list_available_clients
from core.orchestrator import generate_edu_carousel
from api.security import check_any_valid_key, resolve_safe_path, validate_client_scope


# ────────────────────────────────────────────────────
# App setup
# ────────────────────────────────────────────────────

app = FastAPI(
    title="CoinEasy Content Engine",
    description="Multi-tenant Korean content generation for Web3 clients",
    version="1.0.0",
)

API_SECRET = os.environ.get("API_SECRET", "")
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_ROOT", "/tmp/content_engine_output"))


def _check_auth(x_api_key: str):
    check_any_valid_key(x_api_key)


# ────────────────────────────────────────────────────
# Request/Response models
# ────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    source_content: str
    source_type: str = "tweet"  # "tweet" | "blog" | "article"
    source_url: str = ""
    series_number: Optional[str] = None
    mock_llm: bool = False  # for smoke testing


class GenerateResponse(BaseModel):
    client_id: str
    content_type: str
    series: dict
    lesson_count: int
    png_paths: list[str]
    manifest_path: str
    duration_ms: int


class ClientInfo(BaseModel):
    client_id: str
    name: str
    active: bool
    primary_color: str
    features: dict


# ────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "coineasy-content-engine",
        "status": "ok",
        "clients_loaded": len(list_active_clients()),
    }


@app.get("/health")
async def health():
    """Railway health check endpoint."""
    return {"ok": True, "ts": int(time.time())}


@app.get("/clients", response_model=list[ClientInfo])
async def list_clients(x_api_key: str = Header(default="")):
    _check_auth(x_api_key)
    results = []
    for client_id in list_available_clients():
        try:
            cfg = load_client_config(client_id)
            results.append(ClientInfo(
                client_id=cfg.client_id,
                name=cfg.name,
                active=cfg.active,
                primary_color=cfg.brand.primary_color,
                features={
                    "education_carousel": cfg.feature_flags.education_carousel,
                    "news_banner": cfg.feature_flags.news_banner,
                },
            ))
        except Exception as e:
            print(f"⚠ Failed to load client '{client_id}': {e}")
    return results


@app.post("/clients/{client_id}/generate/edu-carousel", response_model=GenerateResponse)
async def generate_carousel(
    client_id: str,
    req: GenerateRequest,
    x_api_key: str = Header(default=""),
):
    """Generate an education carousel for a client."""
    _check_auth(x_api_key)

    # Verify client exists
    try:
        load_client_config(client_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Client '{client_id}' not found")

    # Unique output dir per request
    ts = int(time.time())
    output_dir = OUTPUT_ROOT / client_id / f"edu_{ts}"

    try:
        result = await generate_edu_carousel(
            client_id=client_id,
            source_content=req.source_content,
            source_type=req.source_type,
            source_url=req.source_url,
            series_number=req.series_number,
            output_dir=output_dir,
            mock_llm=req.mock_llm,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Surface full traceback to Railway logs for debugging
        traceback.print_exc()
        raise HTTPException(500, f"Generation failed: {type(e).__name__}: {e}")

    return GenerateResponse(
        client_id=result.client_id,
        content_type=result.content_type,
        series=result.series_meta,
        lesson_count=len(result.png_paths),
        png_paths=result.png_paths,
        manifest_path=result.manifest_path,
        duration_ms=result.duration_ms,
    )


@app.get("/files/{path:path}")
async def serve_file(path: str, x_api_key: str = Header(default="")):
    """Serve generated PNG files with per-client scope validation."""
    safe_path = resolve_safe_path(path)
    validate_client_scope(x_api_key, safe_path)
    if not safe_path.exists() or not safe_path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(safe_path)


# ────────────────────────────────────────────────────
# Startup
# ────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    clients = list_active_clients()
    print(f"✓ Content Engine started with {len(clients)} active clients:")
    for cfg in clients:
        print(f"  - {cfg.client_id:12s} {cfg.name}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
