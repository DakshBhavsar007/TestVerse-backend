"""
TestVerse AI Router — Phase 8A
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from .auth_router import get_current_user
from ..config import get_settings
from ..services.ai_service import (
    generate_test_suggestions,
    detect_anomalies,
    nl_to_api_test,
    ai_chat,
)

router = APIRouter(prefix="/api/ai", tags=["AI — Phase 8A"])


def require_gemini():
    settings = get_settings()
    if not settings.google_gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="AI features require GOOGLE_GEMINI_API_KEY to be set in your .env file."
        )


class SuggestionsRequest(BaseModel):
    history: list[dict]

class AnomalyRequest(BaseModel):
    history: list[dict]

class NLTestRequest(BaseModel):
    prompt: str
    base_url: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    context: Optional[dict] = {}


@router.get("/status")
async def ai_status():
    settings = get_settings()
    key = settings.google_gemini_api_key or ""
    configured = bool(key and len(key) > 10)
    return {
        "configured": configured,
        "model": "gemini-1.5-flash",
        "features": ["suggestions", "anomaly-detection", "nl-to-test", "chat"],
        "message": "AI is ready" if configured else "Set GOOGLE_GEMINI_API_KEY in your .env to enable AI features"
    }


@router.post("/suggestions")
async def get_suggestions(req: SuggestionsRequest, user=Depends(get_current_user)):
    require_gemini()
    try:
        return await generate_test_suggestions(req.history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")


@router.post("/anomalies")
async def get_anomalies(req: AnomalyRequest, user=Depends(get_current_user)):
    require_gemini()
    try:
        return await detect_anomalies(req.history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")


@router.post("/nl-to-test")
async def natural_language_to_test(req: NLTestRequest, user=Depends(get_current_user)):
    require_gemini()
    if not req.prompt or len(req.prompt.strip()) < 5:
        raise HTTPException(status_code=400, detail="Prompt is too short.")
    try:
        return await nl_to_api_test(req.prompt.strip(), req.base_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")


@router.post("/chat")
async def chat(req: ChatRequest, user=Depends(get_current_user)):
    require_gemini()
    if not req.message or len(req.message.strip()) < 2:
        raise HTTPException(status_code=400, detail="Message is too short.")
    try:
        reply = await ai_chat(req.message.strip(), req.context or {})
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")
