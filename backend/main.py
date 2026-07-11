"""FastAPI application entry point.

Single Uvicorn worker only - multiple workers would duplicate model loads
and blow VRAM on an 8GB GPU. Concurrency via asyncio.
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.api.users import router as users_router
from backend.hotpath.ws_session import ConversationSession, SessionConfig
from backend.core.event_bus import EventBus
from backend.core.logging import get_logger, setup_logging
from backend.core.metrics import get_metrics, get_metrics_content_type
from backend.core.model_manager import ModelManager
from backend.core.resource_guard import ResourceGuard
from backend.persistence import close_database, init_database
from config.settings import get_settings

logger = get_logger(__name__)

# Global instances (set during lifespan)
resource_guard: ResourceGuard | None = None
event_bus: EventBus | None = None
model_manager: ModelManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - startup and shutdown."""
    global resource_guard, event_bus, model_manager

    settings = get_settings()
    setup_logging(settings.log_level, json_output=settings.log_level != "DEBUG")

    logger.info(
        "startup_begin",
        host=settings.host,
        port=settings.port,
        resource_ceiling=settings.resource.ceiling,
    )

    # Initialize database
    database = await init_database(settings.database.path)
    app.state.database = database

    # Initialize resource guard
    resource_guard = ResourceGuard(
        ceiling=settings.resource.ceiling,
        soft=settings.resource.soft,
        sample_interval=settings.resource.sample_interval,
        rolling_window=settings.resource.rolling_window,
        hysteresis_margin=settings.resource.hysteresis_margin,
    )
    await resource_guard.start()

    # Initialize event bus
    event_bus = EventBus()
    await event_bus.start()

    # Store in app state for dependency injection
    app.state.resource_guard = resource_guard
    app.state.event_bus = event_bus
    app.state.settings = settings

    # Optionally load AI models (skip with SKIP_MODELS=1 for development)
    skip_models = os.environ.get("SKIP_MODELS", "0") == "1"

    if not skip_models:
        try:
            model_manager = ModelManager(
                guard=resource_guard,
                stt_model=settings.model.stt_model,
                llm_model=settings.model.llm_model,
                tts_model=settings.model.tts_model,
                ollama_host=settings.model.ollama_host,
            )
            await model_manager.initialize()
            app.state.model_manager = model_manager
            logger.info("models_loaded", models=model_manager.models_status)
        except Exception as e:
            logger.warning("models_not_loaded", error=str(e))
            app.state.model_manager = None
    else:
        logger.info("models_skipped", reason="SKIP_MODELS=1")
        app.state.model_manager = None

    logger.info(
        "startup_complete",
        gpu_available=resource_guard.has_gpu,
        database_path=str(settings.database.path),
        models_loaded=model_manager is not None and model_manager.is_initialized,
    )

    yield

    # Shutdown
    logger.info("shutdown_begin")

    if model_manager:
        await model_manager.shutdown()

    await event_bus.stop()
    await resource_guard.stop()
    await close_database()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="English Coach API",
        description="Fully offline AI English Speaking & Listening Coach",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS for frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routers
    app.include_router(users_router)

    @app.get("/")
    async def root() -> dict:
        """Health check endpoint."""
        return {
            "status": "healthy",
            "service": "english-coach",
            "version": "0.1.0",
        }

    @app.get("/health")
    async def health() -> dict:
        """Detailed health check."""
        guard = app.state.resource_guard
        snapshot = guard.snapshot() if guard else None
        mm = getattr(app.state, "model_manager", None)

        return {
            "status": "healthy",
            "resource_guard": {
                "running": guard.is_running if guard else False,
                "gpu_available": guard.has_gpu if guard else False,
                "degradation_level": guard.degradation_level.name if guard else "UNKNOWN",
            },
            "event_bus": {
                "running": app.state.event_bus.is_running if app.state.event_bus else False,
                "queue_size": app.state.event_bus.queue_size if app.state.event_bus else 0,
            },
            "models": mm.models_status if mm and mm.is_initialized else None,
            "resources": snapshot.to_dict() if snapshot else None,
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        return Response(
            content=get_metrics(),
            media_type=get_metrics_content_type(),
        )

    @app.websocket("/ws/conversation/{user_id}")
    async def websocket_conversation(
        websocket: WebSocket,
        user_id: str,
        mode: str = Query(default="free"),
        level: int = Query(default=0, ge=0, le=6),
    ) -> None:
        """WebSocket endpoint for live conversation.

        Handles the real-time audio loop:
        mic → VAD → STT → LLM → TTS → speaker

        Args:
            websocket: WebSocket connection
            user_id: User identifier
            mode: Session mode (free, roleplay, etc.)
            level: Learner CEFR level (0-6)
        """
        mm = getattr(app.state, "model_manager", None)
        guard = app.state.resource_guard
        bus = app.state.event_bus

        if not mm or not mm.is_initialized:
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "error": "Models not loaded. Start server without SKIP_MODELS=1.",
            })
            await websocket.close(code=1011)
            return

        config = SessionConfig(
            user_id=user_id,
            mode=mode,
            learner_level=level,
        )

        session = ConversationSession(
            websocket=websocket,
            config=config,
            model_manager=mm,
            guard=guard,
            event_bus=bus,
        )

        await session.run()

    return app


# Application instance
app = create_app()
