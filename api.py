"""
API Gateway for Universal Social Assistant
"""
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from typing import Dict, Any, List, Optional
from datetime import datetime
from contextlib import asynccontextmanager
import asyncio
import logging
import os
from core.request_context import ensure_request_id, new_request_id, reset_request_id, set_request_id
from core.database import SessionLocal, get_db, check_database_health
from core.models import User
from core.mem0_memory.mem0_module import Mem0MemoryModule, load_mem0_config_from_env
from core.brain import configure_brain_memory
from core.user_system import UserSystemModule
from core.psychology_engine import PsychologyEngineModule
from core.digital_twin import DigitalTwinModule
from core.group_behavior import GroupBehaviorModule
from core.persona_engine import PersonaEngineModule
from core.security import security_layer
from core.orchestrator import Orchestrator
from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine
from core.self_programming import SelfProgrammingModule, set_plugin_registry_for_tools
from core.runtime_diagnostic_module import set_orchestrator_for_runtime_diagnostic
from core.self_healing import SelfHealingEngine
from core.openrouter_provider import get_openrouter_provider
from core.behavior_store import BehaviorStore
from core.diagnostics import build_diagnostic_snapshot
from core.connectivity_check import get_external_connectivity_hints_for_health, log_mem0_startup_status
from core.logging_setup import setup_logging
from core.autopilot_mode import apply_autopilot_defaults, autopilot_enabled
from core.runtime_layout import ensure_runtime_data_layout
import uvicorn

# API process also honors .env and autopilot defaults.
_autopilot_applied = apply_autopilot_defaults()
setup_logging()
ensure_runtime_data_layout()
logger = logging.getLogger(__name__)
if autopilot_enabled():
    logger.info(
        "Autopilot mode enabled (api); defaults applied: %s",
        sorted(_autopilot_applied.keys()),
        extra={"gemma_event": "autopilot_enabled_api", "applied_keys": sorted(_autopilot_applied.keys())},
    )

# API Configuration
from core.api_auth import (
    DEFAULT_API_TOKEN,
    enforce_startup_api_token_config,
    normalize_api_token,
    verify_api_token,
)

API_TOKEN = normalize_api_token(os.getenv("API_TOKEN", DEFAULT_API_TOKEN))
enforce_startup_api_token_config(API_TOKEN)

from core.api_request_limits import API_MESSAGE_MAX_CHARS, validate_relay_meta

class ChatRequest(BaseModel):
    user_id: str
    message: str = Field(..., max_length=API_MESSAGE_MAX_CHARS)
    channel: str = "telegram"
    group_id: Optional[str] = None


class BotRelayRequest(BaseModel):
    """
    Вызов «мозга» с другого сервера / второго бота (Bot API не доставляет сообщения бот→бот).
    Тот же конвейер, что /api/v1/chat; channel по умолчанию bot_relay для различия в логах.
    """

    user_id: str
    message: str = Field(..., max_length=API_MESSAGE_MAX_CHARS)
    channel: str = "bot_relay"
    group_id: Optional[str] = None
    request_id: Optional[str] = None
    source_bot: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    @field_validator("meta")
    @classmethod
    def _validate_meta_size(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return validate_relay_meta(value)


class ChatResponse(BaseModel):
    response: str
    user_id: str
    timestamp: str
    metadata: Dict[str, Any]
    responses: Optional[List[str]] = None

class UserResponse(BaseModel):
    id: int
    external_id: str
    name: str
    username: str
    role: str
    created_at: Optional[str]
    updated_at: Optional[str]

class ChildrenResponse(BaseModel):
    children: List[Dict[str, Any]]


class ScheduleResponse(BaseModel):
    user_id: str
    schedule_items: List[Dict[str, Any]]


class ModuleGenerationRequest(BaseModel):
    module_name: str
    description: str
    commands: Optional[List[Dict[str, Any]]] = None
    dependencies: Optional[List[str]] = None
    pip_requirements: Optional[List[str]] = None
    capabilities: Optional[List[str]] = None

class ModuleGenerationResponse(BaseModel):
    success: bool
    module_name: str
    message: str
    test_result: Optional[bool] = None
    error: Optional[str] = None

class SelfRepairRequest(BaseModel):
    module_name: Optional[str] = None
    library_name: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str = "1.0.0"
    environment: str = os.getenv("APP_ENV", "development")
    database_ok: Optional[bool] = None
    external_services_ok: Optional[bool] = None
    external_services_issues: Optional[List[str]] = None


class DiagnosticResponse(BaseModel):
    status: str
    timestamp: str
    diagnostics: Dict[str, Any]

# Initialize core modules for API access (same wiring as main.py)
def initialize_modules():
    """Initialize modules needed for API"""
    mem0_memory = Mem0MemoryModule(load_mem0_config_from_env())
    configure_brain_memory(mem0_memory)
    try:
        asyncio.run(log_mem0_startup_status(mem0_memory, logger))
    except RuntimeError as e:
        if "running event loop" in str(e).lower():
            logger.warning(
                "Проверка Mem0 при старте пропущена (уже есть event loop). "
                "Состояние ключей смотри в /admin_connectivity или логах бота.",
                extra={"gemma_event": "mem0_startup_check_skipped"},
            )
        else:
            raise

    modules_path = os.getenv("MODULES_PATH", "./modules")
    plugin_registry = PluginRegistry(modules_path)
    from core.plugin_registry import set_plugin_registry

    set_plugin_registry(plugin_registry)
    policy_engine = PolicyEngine()
    openrouter = get_openrouter_provider()
    behavior_store = BehaviorStore()
    group_behavior = GroupBehaviorModule()
    user_system = UserSystemModule()
    psychology_engine = PsychologyEngineModule()
    digital_twin = DigitalTwinModule()
    persona_engine = PersonaEngineModule()
    self_programming = SelfProgrammingModule(modules_path=modules_path)

    orchestrator = Orchestrator(
        plugin_registry=plugin_registry,
        policy_engine=policy_engine,
        openrouter=openrouter,
        mem0_memory=mem0_memory,
        group_behavior=group_behavior,
        behavior_store=behavior_store,
        user_system=user_system,
        psychology_engine=psychology_engine,
        digital_twin=digital_twin,
        persona_engine=persona_engine,
        self_programming=self_programming,
    )
    plugin_registry.load_all_modules()
    set_plugin_registry_for_tools(plugin_registry)
    set_orchestrator_for_runtime_diagnostic(orchestrator)
    try:
        orchestrator._recovery_autonomy.post_boot(orchestrator)
    except Exception as e:
        logger.debug("recovery_autonomy post_boot: %s", e)
    try:
        orchestrator._resilience.post_boot_recovery(orchestrator)
    except Exception as e:
        logger.warning("post_boot_recovery failed: %s", e, exc_info=True)

    self_healing = SelfHealingEngine()
    return mem0_memory, orchestrator, self_healing, plugin_registry

# Initialize modules
mem0_memory, orchestrator, self_healing, plugin_registry = initialize_modules()

from core.api_state import set_orchestrator
from core.ops_trace import install_ops_trace

set_orchestrator(orchestrator)
install_ops_trace()

_healing_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def _api_lifespan(_app: FastAPI):
    global _healing_task
    from core.event_bus import bus as event_bus

    event_bus.start_ff_worker()
    _healing_task = asyncio.create_task(
        self_healing.start_monitoring(plugin_registry),
        name="api-self-healing",
    )
    yield
    if _healing_task and not _healing_task.done():
        _healing_task.cancel()
        try:
            await _healing_task
        except asyncio.CancelledError:
            pass
    await event_bus.shutdown_ff_worker()


app = FastAPI(
    title="Universal Social Assistant API",
    description="API for Universal Social Assistant with social intelligence capabilities",
    version="1.0.0",
    lifespan=_api_lifespan,
)

from core.api_request_limits import RequestBodySizeLimitMiddleware

app.add_middleware(RequestBodySizeLimitMiddleware)

from core.api_ops import ops_router

app.include_router(ops_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Bind correlation id for API logs and downstream orchestrator."""
    incoming = (request.headers.get("X-Request-Id") or "").strip()
    rid = incoming or new_request_id()
    token = set_request_id(rid)
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response
    finally:
        reset_request_id(token)


if os.getenv("API_CORS_ENABLED", "false").lower() == "true":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("API_CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


async def _chat_via_orchestrator(
    *,
    user_id: str,
    message: str,
    channel: str,
    group_id: Optional[str],
    extra_meta: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """Один проход plan + execute_plan (как Telegram, но по HTTP)."""
    from core.models import Input

    rid: Optional[str] = None
    if isinstance(extra_meta, dict):
        rid = str(extra_meta.get("request_id") or extra_meta.get("relay_request_id") or "").strip() or None
    meta: Dict[str, Any] = {
        "user_id": user_id,
        "channel": channel,
        "group_id": group_id,
        "timestamp": datetime.now().isoformat(),
    }
    if extra_meta:
        meta.update(extra_meta)
    meta["request_id"] = ensure_request_id(
        str(meta.get("request_id") or meta.get("relay_request_id") or rid or "").strip() or None
    )
    input_data = Input(type="text", payload=message, meta=meta)
    plan = orchestrator.plan(input_data, user_id, group_id)
    outputs = await orchestrator.execute_plan(plan, user_id, group_id)
    response_text = "Response from assistant"
    all_texts: List[str] = []
    if outputs:
        for o in outputs:
            if o.type == "text" and str(o.payload or "").strip():
                all_texts.append(str(o.payload).strip())
        if all_texts:
            response_text = all_texts[0]
        elif outputs[0].payload:
            response_text = str(outputs[0].payload)
        else:
            response_text = "Empty response"
    md: Dict[str, Any] = {
        "channel": channel,
        "group_id": group_id,
        "outputs_count": len(outputs) if outputs else 0,
        "responses": all_texts,
        "bot_instance": (os.getenv("BOT_INSTANCE_ID") or "").strip() or None,
    }
    try:
        md["plan_steps"] = len(plan.steps) if getattr(plan, "steps", None) else 0
    except Exception:
        pass
    return response_text, md


@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    db = check_database_health()
    hints = get_external_connectivity_hints_for_health()
    issues = [str(x) for x in (hints.get("failure_messages") or []) if x]
    if not db.get("ok"):
        issues.insert(0, f"database: {db.get('error') or 'unavailable'}")
    degraded = bool(issues)
    has_any = bool(hints.get("by_service"))
    payload = HealthResponse(
        status="unhealthy" if not db.get("ok") else ("degraded" if degraded else "healthy"),
        timestamp=datetime.now().isoformat(),
        database_ok=bool(db.get("ok")),
        external_services_ok=(not degraded) if has_any else None,
        external_services_issues=issues if issues else None,
    )
    if not db.get("ok"):
        return JSONResponse(status_code=503, content=payload.model_dump())
    return payload


@app.get("/api/v1/diagnostics", response_model=DiagnosticResponse)
async def diagnostics_endpoint(token: str = Depends(verify_api_token)):
    """Unified diagnostics snapshot endpoint."""
    snap = build_diagnostic_snapshot(orchestrator)
    return DiagnosticResponse(
        status="ok",
        timestamp=datetime.now().isoformat(),
        diagnostics=snap,
    )

@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat_endpoint(
    http_request: Request,
    request: ChatRequest,
    _token: str = Depends(verify_api_token),
):
    """Send message to assistant and get response through orchestrator"""
    from core.api_rate_limit import assert_api_heavy_rate_limit

    await assert_api_heavy_rate_limit(http_request, user_id=request.user_id)
    try:
        response_text, md = await _chat_via_orchestrator(
            user_id=request.user_id,
            message=request.message,
            channel=request.channel,
            group_id=request.group_id,
            extra_meta=None,
        )
        md["context_assembled"] = True
        return ChatResponse(
            response=response_text,
            user_id=request.user_id,
            timestamp=datetime.now().isoformat(),
            metadata=md,
            responses=md.get("responses"),
        )
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/v1/bot-relay/invoke", response_model=ChatResponse)
async def bot_relay_invoke(
    http_request: Request,
    request: BotRelayRequest,
    _token: str = Depends(verify_api_token),
):
    """
    Вызов второго инстанса (другой сервер): тот же оркестратор, без доставки через Telegram бот→бот.
    Авторизация: API_TOKEN или BOT_RELAY_API_TOKEN (если задан отдельный секрет для пиров).
    """
    from core.api_rate_limit import assert_api_heavy_rate_limit

    await assert_api_heavy_rate_limit(http_request, user_id=request.user_id)
    try:
        extra: Dict[str, Any] = {}
        if request.request_id:
            extra["relay_request_id"] = request.request_id
        if request.source_bot:
            extra["relay_source_bot"] = request.source_bot
        if request.meta:
            extra["relay_meta"] = request.meta
        response_text, md = await _chat_via_orchestrator(
            user_id=request.user_id,
            message=request.message,
            channel=request.channel,
            group_id=request.group_id,
            extra_meta=extra or None,
        )
        md["relay"] = True
        if request.request_id:
            md["request_id"] = request.request_id
        if request.source_bot:
            md["source_bot"] = request.source_bot
        return ChatResponse(
            response=response_text,
            user_id=request.user_id,
            timestamp=datetime.now().isoformat(),
            metadata=md,
            responses=md.get("responses"),
        )
    except Exception as e:
        logger.error("bot-relay invoke error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, token: str = Depends(verify_api_token), db=Depends(get_db)):
    """Get user profile"""
    try:
        user = db.query(User).filter(User.external_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return UserResponse(
            id=user.id,
            external_id=user.external_id,
            name=user.name,
            username=user.username,
            role=user.role,
            created_at=user.created_at.isoformat() if user.created_at else None,
            updated_at=user.updated_at.isoformat() if user.updated_at else None
        )
    except Exception as e:
        logger.error(f"Get user error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/parents/{parent_id}/children", response_model=ChildrenResponse)
async def get_children(parent_id: str, token: str = Depends(verify_api_token)):
    """Parent/child relationships are not implemented in the public API."""
    raise HTTPException(status_code=501, detail="Not implemented")

@app.get("/api/v1/schedule/{user_id}", response_model=ScheduleResponse)
async def get_schedule(user_id: str, token: str = Depends(verify_api_token)):
    """User schedule API is not implemented in the public build."""
    raise HTTPException(status_code=501, detail="Not implemented")

@app.post("/api/v1/generate-module", response_model=ModuleGenerationResponse)
async def generate_module_endpoint(request: ModuleGenerationRequest, token: str = Depends(verify_api_token)):
    """Generate a new module based on a description"""
    try:
        if not getattr(orchestrator, "self_programming", None):
            raise HTTPException(status_code=503, detail="Self-programming engine not available")
        result = await orchestrator.self_programming.generate_module(
            request.module_name,
            request.description,
            request.commands,
            request.dependencies,
            request.pip_requirements,
            capabilities=request.capabilities,
        )
        
        # Convert result to our response format
        response = ModuleGenerationResponse(
            success=result.get("success", False),
            module_name=result.get("module_name", ""),
            message=result.get("message", ""),
            test_result=result.get("test_result"),
            error=result.get("error")
        )
        
        return response
    except Exception as e:
        logger.error(f"Error generating module: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/self-repair", response_model=Dict[str, Any])
async def self_repair_endpoint(request: SelfRepairRequest, token: str = Depends(verify_api_token)):
    """Repair a failing module or library"""
    try:
        if not getattr(orchestrator, "self_programming", None):
            raise HTTPException(status_code=503, detail="Self-programming engine not available")
        if request.module_name:
            result = await orchestrator.self_programming.self_repair_module(request.module_name)
        elif request.library_name:
            result = await orchestrator.self_programming.self_repair_library(request.library_name)
        else:
            raise HTTPException(status_code=400, detail="Either module_name or library_name must be specified")
        
        # Convert result to standard response format
        return {
            "success": result.get("success", False),
            "message": result.get("message", ""),
            "error": result.get("error")
        }
    except Exception as e:
        logger.error(f"Error in self-repair: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Health check endpoint (non-API)
@app.get("/health")
async def health():
    db = check_database_health()
    hints = get_external_connectivity_hints_for_health()
    issues = [str(x) for x in (hints.get("failure_messages") or []) if x]
    if not db.get("ok"):
        issues.insert(0, f"database: {db.get('error') or 'unavailable'}")
    out: Dict[str, Any] = {
        "status": "unhealthy" if not db.get("ok") else ("degraded" if issues else "healthy"),
        "version": "1.0.0",
        "database_ok": bool(db.get("ok")),
    }
    if issues:
        if db.get("ok"):
            out["status"] = "degraded"
        out["external_services_issues"] = issues
    if not db.get("ok"):
        return JSONResponse(status_code=503, content=out)
    return out

# Error handlers
@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={"detail": "Not found"}
    )

@app.exception_handler(401)
async def unauthorized_handler(request, exc):
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"}
    )

@app.exception_handler(500)
async def internal_error_handler(request, exc):
    logger.error(f"Internal error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )