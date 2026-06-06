"""
API Gateway for Universal Social Assistant
"""
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from datetime import datetime
from contextlib import asynccontextmanager
import asyncio
import logging
import os
from core.database import SessionLocal, get_db
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
from core.api_auth import allowed_api_tokens, normalize_api_token, verify_api_token

API_TOKEN = normalize_api_token(os.getenv("API_TOKEN", "your_secure_api_token_here"))
if API_TOKEN == "your_secure_api_token_here":
    logger.warning("Using default API token - this is insecure in production!")

class ChatRequest(BaseModel):
    user_id: str
    message: str
    channel: str = "telegram"
    group_id: Optional[str] = None


class BotRelayRequest(BaseModel):
    """
    Вызов «мозга» с другого сервера / второго бота (Bot API не доставляет сообщения бот→бот).
    Тот же конвейер, что /api/v1/chat; channel по умолчанию bot_relay для различия в логах.
    """

    user_id: str
    message: str
    channel: str = "bot_relay"
    group_id: Optional[str] = None
    request_id: Optional[str] = None
    source_bot: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


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

from core.api_ops import ops_router

app.include_router(ops_router)

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

    meta: Dict[str, Any] = {
        "user_id": user_id,
        "channel": channel,
        "group_id": group_id,
        "timestamp": datetime.now().isoformat(),
    }
    if extra_meta:
        meta.update(extra_meta)
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
    hints = get_external_connectivity_hints_for_health()
    issues = [str(x) for x in (hints.get("failure_messages") or []) if x]
    degraded = bool(issues)
    has_any = bool(hints.get("by_service"))
    return HealthResponse(
        status="degraded" if degraded else "healthy",
        timestamp=datetime.now().isoformat(),
        external_services_ok=(not degraded) if has_any else None,
        external_services_issues=issues if issues else None,
    )


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
async def get_children(parent_id: str, token: str = Depends(verify_api_token), db=Depends(get_db)):
    """Get children of a parent"""
    try:
        # Find parent by user_id
        parent = db.query(User).filter(User.external_id == parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent not found")
        
        # Here we would query parent-child relationships in real system
        # For now, returning mock response but keeping proper structure
        children_list = []
        
        # In a production system, this would query the actual DB relationships:
        # children_list = [
        #     {
        #         "id": child.id,
        #         "external_id": child.external_id,
        #         "name": child.name,
        #         "username": child.username,
        #         "role": child.role,
        #         "created_at": child.created_at.isoformat() if child.created_at else None
        #     }
        #     for child in db.query(User).join(ParentChildLink).filter(ParentChildLink.parent_id == parent.id).all()
        # ]
        
        children_list.append({
            "id": "child_123",
            "external_id": "child_123",
            "name": "Дети",
            "username": "",
            "role": "child",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        })
        
        return ChildrenResponse(children=children_list)
    except Exception as e:
        logger.error(f"Get children error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/schedule/{user_id}", response_model=ScheduleResponse)
async def get_schedule(user_id: str, token: str = Depends(verify_api_token), db=Depends(get_db)):
    """Get user schedule"""
    try:
        # Find user
        user = db.query(User).filter(User.external_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # In a full implementation, we would query actual schedule data
        # Here using mock data to maintain API contract
        schedule_items = [
            {
                "id": "lesson_1",
                "datetime_start": "2026-04-25T09:00:00",
                "datetime_end": "2026-04-25T10:00:00",
                "type": "lesson",
                "description": "Математика"
            }
        ]
        
        return ScheduleResponse(
            user_id=user_id,
            schedule_items=schedule_items
        )
    except Exception as e:
        logger.error(f"Get schedule error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

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
    hints = get_external_connectivity_hints_for_health()
    issues = [str(x) for x in (hints.get("failure_messages") or []) if x]
    out: Dict[str, Any] = {"status": "healthy", "version": "1.0.0"}
    if issues:
        out["status"] = "degraded"
        out["external_services_issues"] = issues
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