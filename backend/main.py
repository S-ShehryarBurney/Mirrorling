from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, model_validator
from database import lifespan
from identity_routes import router as identity_router
from memory_routes import router as memory_router
from llm_provider import client, MODEL, ProviderError
from identity_service import (
    IdentityResourceNotFound,
    InvalidFirstMeetingTransition,
    RelationshipUpdateConflict,
    get_companion,
    get_relationship,
    update_first_meeting_status,
)
from memory_models import FirstMeetingStatus
from memory_retrieval import build_memory_context, retrieve_relevant_memories
from familiarity_service import (
    build_familiarity_candidate_context,
    get_pending_candidate_for_chat,
)
from pymongo.errors import PyMongoError
from typing import Any
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID
from speech_to_text import transcribe_audio
from fastapi.responses import Response
from speech_output import (
    SpeechProviderError,
    generate_speech,
    list_elevenlabs_voices,
)
from provider_config import PROVIDER_CONFIG
from personality import SYSTEM_PROMPT, build_companion_system_prompt
from emotion_service import analyze_face_image
from voice_analysis import analyze_voice

app = FastAPI(lifespan=lifespan)
app.include_router(identity_router)
app.include_router(memory_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    session_id: str
    perception: "PerceptionData | None" = None
    user_id: UUID | None = None
    companion_id: str | None = None

    @model_validator(mode="after")
    def require_user_for_companion(self):
        if self.companion_id is not None and self.user_id is None:
            raise ValueError("user_id is required when companion_id is supplied")
        return self

class PerceptionSource(BaseModel):
    captured_at: int | None = None
    measurements: dict[str, Any] = Field(default_factory=dict)

class PerceptionData(BaseModel):
    voice: PerceptionSource | None = None
    face: PerceptionSource | None = None

conversations = {}

MAX_HISTORY_MESSAGES = 20

def build_perception_context(perception: PerceptionData | None) -> str:
    if perception is None:
        return ""

    sources = []

    if perception.voice and perception.voice.measurements:
        sources.append(
            "Voice measurements (uncertain estimates): "
            + json.dumps(perception.voice.measurements, separators=(",", ":"))
        )

    if perception.face and perception.face.measurements:
        sources.append(
            "Facial-expression estimates (uncertain estimates): "
            + json.dumps(perception.face.measurements, separators=(",", ":"))
        )

    if not sources:
        return ""

    return (
        "\n\nPerception context:\n"
        "The user's actual words take priority. These are uncertain model estimates; "
        "a single measurement cannot establish an emotion or mental state. Never "
        "diagnose or claim to know how the user feels. Mention a possible mismatch "
        "gently and naturally only when relevant, and do not force a perception-based "
        "observation into every response.\n"
        + "\n".join(sources)
    )


def get_chat_history_key(request: ChatRequest, companion_id: str | None = None):
    if request.user_id is None:
        return request.session_id
    return (
        str(request.user_id),
        companion_id,
        request.session_id,
    )

class SpeechRequest(BaseModel):
    text: str
    companion_id: str | None = Field(default=None, max_length=80)
    voice_id: str | None = Field(default=None, max_length=200)

@app.post("/chat")
def chat(request: ChatRequest, http_request: Request):

    memories = []
    familiarity_candidate = None
    active_companion = None
    relationship = None
    first_meeting_prompt_status = None
    if request.user_id is not None:
        database = getattr(http_request.app.state, "database", None)
        if database is None or not getattr(
            http_request.app.state,
            "memory_store_initialized",
            False,
        ):
            raise HTTPException(
                status_code=503,
                detail="Memory storage is unavailable",
            )
        try:
            if request.companion_id is not None:
                active_companion = get_companion(
                    database,
                    str(request.user_id),
                    request.companion_id,
                )
                relationship = get_relationship(
                    database,
                    str(request.user_id),
                    active_companion.companion_id,
                )
                first_meeting_prompt_status = relationship.first_meeting_status
            memories = retrieve_relevant_memories(
                database,
                str(request.user_id),
                request.message,
                (
                    active_companion.companion_id
                    if active_companion is not None
                    else None
                ),
            )
            if active_companion is not None:
                familiarity_candidate = get_pending_candidate_for_chat(
                    database,
                    str(request.user_id),
                    active_companion.companion_id,
                )
        except IdentityResourceNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValidationError as error:
            raise HTTPException(
                status_code=503,
                detail="Stored memory data is unavailable",
            ) from error
        except PyMongoError as error:
            raise HTTPException(
                status_code=503,
                detail="Memory retrieval is temporarily unavailable",
            ) from error

    if (
        active_companion is not None
        and relationship is not None
        and relationship.first_meeting_status == FirstMeetingStatus.NOT_STARTED
    ):
        try:
            update_first_meeting_status(
                http_request.app.state.database,
                str(request.user_id),
                active_companion.companion_id,
                FirstMeetingStatus.IN_PROGRESS,
            )
        except IdentityResourceNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (InvalidFirstMeetingTransition, RelationshipUpdateConflict) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except PyMongoError as error:
            raise HTTPException(
                status_code=503,
                detail="Relationship status is temporarily unavailable",
            ) from error

    history_key = get_chat_history_key(
        request,
        active_companion.companion_id if active_companion is not None else None,
    )
    if history_key not in conversations:
        conversations[history_key] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]

    history = conversations[history_key]

    history.append({
        "role": "user",
        "content": request.message + build_perception_context(request.perception)
    })

    llm_history = [message.copy() for message in history]
    llm_history[0]["content"] = build_companion_system_prompt(
        active_companion,
        first_meeting_prompt_status,
    )
    temporary_context = [
        build_memory_context(memories),
        build_familiarity_candidate_context(familiarity_candidate),
    ]
    temporary_context = [context for context in temporary_context if context]
    if temporary_context:
        llm_history[-1]["content"] += "\n\n" + "\n\n".join(
            temporary_context
        )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=llm_history,
            max_tokens=512,
            temperature=0.85,
            top_p=0.9
        )
    except ProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="The configured LLM provider is unavailable.",
        ) from error

    reply = response.choices[0].message.content

    history.append({
        "role": "assistant",
        "content": reply
    })

    if len(history) > MAX_HISTORY_MESSAGES + 1:
        del history[1:3]

    return {"reply": reply}

@app.post("/speak")
def speak(request: SpeechRequest):
    try:
        audio, media_type = generate_speech(
            request.text,
            companion_id=request.companion_id,
            voice_id=request.voice_id,
        )
    except SpeechProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error

    return Response(
        content=audio,
        media_type=media_type,
    )


@app.get("/speech/voices")
def speech_voices():
    try:
        voices = list_elevenlabs_voices()
    except SpeechProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error

    return {
        "provider": PROVIDER_CONFIG.tts_provider,
        "fallback_provider": PROVIDER_CONFIG.tts_fallback_provider,
        "elevenlabs_configured": bool(PROVIDER_CONFIG.elevenlabs_api_key),
        "voices": voices,
        "default_voice_ids": PROVIDER_CONFIG.elevenlabs_voice_ids,
    }

@app.post("/transcribe")
def transcribe(file: UploadFile = File(...)):
    audio_data = file.file.read()

    if not audio_data:
        raise HTTPException(
            status_code=400,
            detail="Empty audio file"
        )

    suffix = Path(file.filename or "audio.webm").suffix or ".webm"

    with tempfile.NamedTemporaryFile(
        delete=False, suffix=suffix
    ) as temp_file:
        temp_file.write(audio_data)
        temp_path = temp_file.name

    try:
        # Step 1: Convert speech into text.
        text = transcribe_audio(temp_path)

        # Step 2: Analyse the vocal characteristics.
        try:
            voice_features = analyze_voice(temp_path, text)

        except Exception as error:
            print("Voice analysis error:", error)
            voice_features = None

        # Return both results.
        return {
            "transcription": text,
            "voice_analysis": voice_features
        }

    finally:
        os.remove(temp_path)

@app.post("/analyze-face")
def analyze_face(file: UploadFile = File(...)):

    image_data = file.file.read(2 * 1024 * 1024 + 1)

    if not image_data:
        raise HTTPException(
            status_code=400,
            detail="Empty image received."
        )

    if len(image_data) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="Image is too large."
        )

    try:
        result = analyze_face_image(image_data)
        return result

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error

    except Exception as error:
        print("Facial analysis error:", error)

        raise HTTPException(
            status_code=500,
            detail="Facial analysis failed."
        ) from error

@app.get("/")
def home():
    return {"Message": "Mirrorling backend is running!"}

@app.get("/health")
def health(request: Request):
    mongo_client = getattr(request.app.state, "mongo_client", None)
    mongodb_connected = False
    if mongo_client is not None:
        try:
            ping_result = mongo_client.admin.command("ping")
            mongodb_connected = (
                isinstance(ping_result, dict)
                and ping_result.get("ok") == 1
            )
        except PyMongoError:
            mongodb_connected = False
    request.app.state.mongodb_connected = mongodb_connected
    return {
        "application": "running",
        "mongodb": "connected" if mongodb_connected else "unavailable",
    }
