from __future__ import annotations

import os
import threading

from dotenv import load_dotenv
from google import genai
from google.genai import types

from .prompts import SYSTEM_PROMPT

load_dotenv()


class AgentService:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        self.client = genai.Client(api_key=api_key)
        self.chats = {}
        self.lock = threading.Lock()

    def _get_chat(self, user_id: int, conversation_id: str):
        chat_key = (user_id, conversation_id)
        with self.lock:
            if chat_key not in self.chats:
                self.chats[chat_key] = self.client.chats.create(
                    model=os.getenv("AGENT_MODEL", "models/gemini-3.5-flash"),
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.2,
                    ),
                )
            return self.chats[chat_key]

    def send(self, user_id: int, conversation_id: str, message: str) -> str:
        response = self._get_chat(user_id, conversation_id).send_message(message)
        return getattr(response, "text", "") or "The agent returned an empty response."


_agent_service = None


def send_agent_message(user_id: int, conversation_id: str, message: str) -> str:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service.send(user_id, conversation_id, message)