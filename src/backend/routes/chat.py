"""Chat with the agent, its conversations, and the activity log: the conversation list, creating and deleting a
conversation, writing a log entry, and sending a chat message. The chat's limits live as constants on main (tests
tune them) and the shared helpers (`_incident_context`, `send_agent_message`, `_warn_about_budget`) stay there too;
this module is the HTTP layer."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from sqlalchemy import func

from .. import main
from ...agent import settings as agent_settings, usage as agent_usage
from ..config import SESSION_COOKIE_NAME
from ..db import get_db
from ..models import AuditLog, ChatMessage, Conversation, User

router = APIRouter()


@router.get("/logs")
def list_logs(
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    conversation_id: str | None = Query(default=None),
    db=Depends(get_db),
):
    user = main.get_user_from_session(main.redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")

    return {"logs": main.get_recent_logs(db, limit=100, user_id=user["id"], conversation_id=conversation_id)}

def _conversation_payload(conversation: Conversation, messages: list[ChatMessage]) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "messages": [{"id": item.id, "role": item.role, "text": item.content, "created_at": item.created_at.isoformat()} for item in messages],
    }

@router.get("/conversations")
def list_conversations(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main.get_user_from_session(main.redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    conversations = db.query(Conversation).filter(Conversation.user_id == user["id"]).order_by(Conversation.updated_at.desc()).all()
    return {"conversations": [_conversation_payload(item, db.query(ChatMessage).filter(ChatMessage.conversation_id == item.id).order_by(ChatMessage.created_at).all()) for item in conversations]}

@router.post("/conversations")
def create_conversation(payload: main.ConversationRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main.get_user_from_session(main.redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    main.enforce_limit(main.redis_client, scope="conversation-user", subject=user["id"], limit=main.CONVERSATION_CREATE_LIMIT, window_seconds=3600)
    if db.query(Conversation).filter(Conversation.id == payload.conversation_id).first():
        raise HTTPException(status_code=409, detail="Conversation already exists.")
    if db.query(func.count(Conversation.id)).filter(Conversation.user_id == user["id"]).scalar() >= main.MAX_CONVERSATIONS_PER_USER:
        raise HTTPException(status_code=409, detail=f"You already have {main.MAX_CONVERSATIONS_PER_USER} conversations. Delete some before starting a new one.")
    conversation = Conversation(id=payload.conversation_id, user_id=user["id"], title=payload.title.strip()[:255] or "New chat")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_payload(conversation, [])

@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main.get_user_from_session(main.redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.user_id == user["id"]).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.user_id == user["id"], AuditLog.conversation_id == conversation_id).delete(synchronize_session=False)
    db.delete(conversation)
    db.commit()
    main.delete_agent_conversation(user["id"], conversation_id)
    return {"status": "deleted"}

@router.post("/logs")
def create_log(
    payload: main.LogCreateRequest,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db=Depends(get_db),
):
    user = main.get_user_from_session(main.redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    main.enforce_limit(main.redis_client, scope="logs-user", subject=user["id"], limit=main.LOG_WRITE_LIMIT, window_seconds=3600)

    return main.record_audit_log(
        db,
        user_id=user["id"],
        level=payload.level,
        message=payload.message,
        source="dashboard",
        task_title=payload.task_title or payload.message[:255],
        steps=[step for step in payload.steps if step.strip()],
        conversation_id=payload.conversation_id,
        event_type=payload.event_type or None,
    )

@router.post("/agent/chat")
def agent_chat(payload: main.AgentChatRequest, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main.get_user_from_session(main.redis_client, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    if not main.is_agent_chat_enabled(db):
        raise HTTPException(status_code=403, detail="Chat with the agent has been turned off by an admin.")
    account = db.get(User, user["id"])
    if account is None or not agent_settings.may_chat(db, account):
        raise HTTPException(status_code=403, detail="Chat with the agent is not switched on for your account. An admin can allow it in Admin › Users.")
    main.enforce_limit(main.redis_client, scope="chat-user", subject=user["id"], limit=main.CHAT_MESSAGE_LIMIT, window_seconds=3600)
    spend = agent_usage.budget(db)
    if spend["state"] == "paused":
        raise HTTPException(status_code=429, detail=f"The agent's budget for this month is used up (${spend['cost']:.2f} of ${spend['cap']:.2f}). An admin can raise it in Admin › Incident agent.")
    main.enforce_limit(main.redis_client, scope="chat-user-day", subject=user["id"], limit=agent_usage.get_daily_limit(db), window_seconds=86400)

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required.")
    conversation = db.query(Conversation).filter(Conversation.id == payload.conversation_id, Conversation.user_id == user["id"]).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content=message))
    if conversation.title == "New chat":
        conversation.title = message[:255]
    db.commit()

    def log_usage(completion, duration_ms: int) -> None:
        agent_usage.record(db, kind="chat", model=completion.model, input_tokens=completion.input_tokens, output_tokens=completion.output_tokens, duration_ms=duration_ms,
                           user_id=user["id"], conversation_id=payload.conversation_id, incident_id=payload.incident_id)

    try:
        response_text = main.send_agent_message(
            user["id"],
            payload.conversation_id,
            main._incident_context(db, payload.incident_id) + message,
            usage_logger=log_usage,
            command_logger=lambda event: main.record_audit_log(
                db,
                user_id=user["id"],
                source="health_monitor",
                task_title=message[:255],
                steps=event.get("steps", []),
                conversation_id=payload.conversation_id,
                event_type=event.get("event_type"),
                message=event.get("message", "Command event"),
                level=event.get("level", "INFO"),
            ),
        )
    except Exception as exc:
        user_message = main.agent_error_message(exc)
        agent_usage.record(db, kind="chat", status="error", model=main.AGENT_MODEL, user_id=user["id"], conversation_id=payload.conversation_id, incident_id=payload.incident_id, error=user_message)
        main.record_audit_log(
            db,
            user_id=user["id"],
            level="ERROR",
            message=user_message[:255],
            source="health_monitor",
            task_title=message[:255],
            steps=["The AI provider request failed before a health check could run."],
            conversation_id=payload.conversation_id,
            event_type="agent_error",
        )
        db.add(ChatMessage(conversation_id=conversation.id, role="agent", content=f"Error: {user_message}"))
        db.commit()
        raise HTTPException(status_code=503, detail={"message": user_message}) from exc

    steps = ["Received the task and evaluated the request.", "Generated a structured response for the requested task."]
    main._warn_about_budget(db, spend)

    main.record_audit_log(
        db,
        user_id=user["id"],
        level="INFO",
        message=response_text.splitlines()[0][:255] if response_text.splitlines() else "Agent task completed",
        source="homie",
        task_title=message[:255],
        steps=steps,
        conversation_id=conversation.id,
    )
    db.add(ChatMessage(conversation_id=conversation.id, role="agent", content=response_text))
    db.commit()
    return {"message": response_text}
