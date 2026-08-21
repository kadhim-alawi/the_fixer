"""Mission Control HTTP API.

Serves the console and streams a running mission over SSE. Chosen over
websockets because the traffic is one-directional and SSE reconnects by itself,
which matters when the thing being watched is a live demo.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agent import model as model_cfg
from ..sim import incidents as I
from .missions import OBJECTIVE, manager

app = FastAPI(title="The Fixer -- Mission Control")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "dist"


class StartRequest(BaseModel):
    objective: str = OBJECTIVE
    incident_key: str = "payment_config_regression"
    seed: int = 4242
    speed: float = 180.0
    agent: str | None = None


@app.get("/api/health")
async def health() -> dict:
    b = model_cfg.resolve()
    return {
        "ok": True,
        "model_backend": b.kind,
        "model": b.model if b.ready else None,
        "agent_default": "llm" if b.ready else "oracle",
        "detail": b.detail,
    }


@app.get("/api/incidents")
async def incidents() -> dict:
    """The scenarios an operator can start.

    Deliberately exposes only the key and a neutral label. The console must not
    be able to tell anyone -- including a demo audience -- what the answer is.
    """
    return {
        "incidents": [
            {"key": k, "label": k.replace("_", " ")} for k in I.ALL_KEYS
        ]
    }


@app.post("/api/missions")
async def start_mission(req: StartRequest) -> dict:
    if req.incident_key not in I.REGISTRY:
        raise HTTPException(404, f"unknown incident {req.incident_key}")
    s = await manager.start(
        objective=req.objective,
        incident_key=req.incident_key,
        seed=req.seed,
        speed=req.speed,
        agent=req.agent,
    )
    return s.snapshot()


@app.get("/api/missions/{mid}")
async def get_mission(mid: str) -> dict:
    s = manager.get(mid)
    if not s:
        raise HTTPException(404, "no such mission")
    return s.snapshot()


@app.get("/api/missions/{mid}/metrics")
async def get_metrics(mid: str, hours: int = 6) -> dict:
    s = manager.get(mid)
    if not s:
        raise HTTPException(404, "no such mission")
    return await manager.metrics(s, hours=hours)


class Decision(BaseModel):
    approval_id: str
    approved: bool


@app.post("/api/missions/{mid}/approve")
async def approve(mid: str, d: Decision) -> dict:
    if not manager.decide(mid, d.approval_id, d.approved):
        raise HTTPException(404, "no such mission or approval")
    return manager.get(mid).snapshot()  # type: ignore[union-attr]


@app.get("/api/missions/{mid}/stream")
async def stream(mid: str) -> StreamingResponse:
    s = manager.get(mid)
    if not s:
        raise HTTPException(404, "no such mission")

    async def gen():
        q = s.subscribe()
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Keeps proxies and Cloud Run from closing an idle stream
                    # while the agent is thinking.
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(payload, default=str)}\n\n"
                if payload.get("type") == "finished":
                    break
        finally:
            s.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- static frontend --------------------------------------------------------

if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        target = FRONTEND / path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND / "index.html")


def main() -> None:
    import uvicorn

    from .missions import MISSION_DIR

    MISSION_DIR.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "fixer.api.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=False,
    )


if __name__ == "__main__":
    main()
