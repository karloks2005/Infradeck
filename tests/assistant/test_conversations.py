from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.backend.models import AuditLog, Base, ChatMessage, Conversation


def test_conversation_records_are_independent_for_safe_deletion():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Conversation(id="delete-me", user_id=1, title="Delete me"),
        Conversation(id="keep-me", user_id=1, title="Keep me"),
        ChatMessage(conversation_id="delete-me", role="user", content="Remove this"),
        ChatMessage(conversation_id="keep-me", role="user", content="Keep this"),
        AuditLog(user_id=1, conversation_id="delete-me", level="INFO", message="Old event", source="health_monitor"),
        AuditLog(user_id=1, conversation_id="keep-me", level="INFO", message="Other event", source="health_monitor"),
    ])
    session.commit()

    session.query(ChatMessage).filter(ChatMessage.conversation_id == "delete-me").delete()
    session.query(AuditLog).filter(AuditLog.user_id == 1, AuditLog.conversation_id == "delete-me").delete()
    session.query(Conversation).filter(Conversation.id == "delete-me", Conversation.user_id == 1).delete()
    session.commit()

    assert session.query(Conversation).filter_by(id="keep-me").count() == 1
    assert session.query(ChatMessage).filter_by(conversation_id="keep-me").count() == 1
    assert session.query(AuditLog).filter_by(conversation_id="keep-me").count() == 1
