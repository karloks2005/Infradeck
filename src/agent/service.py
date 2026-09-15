from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types

from .prompts import SYSTEM_PROMPT
from ..guardrails.guardrails_engine import GuardrailEngine

load_dotenv()


class AgentService:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        self.client = genai.Client(api_key=api_key)
        self.chats = {}
        self.pending_commands = {}
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

    def send(
        self,
        user_id: int,
        conversation_id: str,
        message: str,
        command_logger: Callable[[dict], None] | None = None,
    ) -> str:
        chat = self._get_chat(user_id, conversation_id)

        pending_key = (user_id, conversation_id)
        if message.strip().upper() == "[APPROVE]" and pending_key in self.pending_commands:
            pending = self.pending_commands.pop(pending_key)
            self._log_event(command_logger, {
                "event_type": "approval",
                "message": "Human approval granted for command",
                "approval_status": "approved",
                **pending,
            })
            response_text = self._response_text(chat.send_message("[APPROVED] Execute the pending command now."))
            system_output = self._execute_and_log(pending, command_logger)
            return self._response_text(chat.send_message(system_output))

        response_text = self._response_text(chat.send_message(message))

        for _ in range(3):
            command_data = self._extract_command(response_text)
            if not command_data:
                return response_text

            command, risk = command_data
            reason = self._extract_reason(response_text)
            event = {
                "event_type": "command_proposed",
                "message": "Agent proposed a command",
                "command": command,
                "reason": reason,
                "risk": risk,
                "approval_status": "not_required",
                "steps": ["Extracted command, reason, and reported risk from agent response."],
            }
            evaluation = GuardrailEngine(command, risk).evaluate()
            if not evaluation["allowed"]:
                event.update({
                    "event_type": "command_blocked",
                    "message": "Guardrails blocked the proposed command",
                    "approval_status": "blocked",
                    "steps": [evaluation["reason"]],
                    "level": "WARN",
                })
                self._log_event(command_logger, event)
                return f"{response_text}\n\n[EXECUTION BLOCKED]: {evaluation['reason']}"
            if evaluation.get("requires_human_approval"):
                event.update({
                    "approval_status": "pending",
                    "steps": [*event["steps"], "Waiting for explicit human approval before execution."],
                })
                self.pending_commands[pending_key] = {
                    "command": command,
                    "reason": reason,
                    "risk": evaluation["effective_risk"],
                    "approval_status": "approved",
                }
                self._log_event(command_logger, event)
                return f"{response_text}\n\n[APPROVAL REQUIRED]: This command has {evaluation['effective_risk']} risk and needs explicit approval."

            event["approval_status"] = "not_required"
            self._log_event(command_logger, event)
            system_output = self._execute_and_log({"command": command, "reason": reason, "risk": evaluation["effective_risk"]}, command_logger)
            response_text = self._response_text(chat.send_message(system_output))

        return response_text

    @staticmethod
    def _response_text(response) -> str:
        return getattr(response, "text", "") or "The agent returned an empty response."

    @staticmethod
    def _extract_command(response_text: str) -> tuple[str, str] | None:
        command_match = re.search(r"^\[COMMAND\]:\s*(.+)$", response_text, re.MULTILINE)
        if not command_match:
            return None
        risk_match = re.search(r"^\[RISK\]:\s*(VERY LOW|LOW|MEDIUM|HIGH|VERY HIGH)\s*$", response_text, re.MULTILINE)
        #Make sure risk defaults to "VERY HIGH" if not specified to ensure safety
        return command_match.group(1).strip(), risk_match.group(1) if risk_match else "VERY HIGH"

    @staticmethod
    def _extract_reason(response_text: str) -> str:
        reason_match = re.search(r"^\[REASON\]:\s*(.+)$", response_text, re.MULTILINE)
        return reason_match.group(1).strip() if reason_match else "No reason provided by agent."

    @staticmethod
    def _log_event(command_logger: Callable[[dict], None] | None, event: dict):
        if command_logger:
            command_logger(event)

    def _execute_and_log(self, command_data: dict, command_logger: Callable[[dict], None] | None) -> str:
        system_output = self._run_command(command_data["command"])
        exit_code = int(re.search(r"^\[EXIT_CODE\]:\s*(-?\d+)$", system_output, re.MULTILINE).group(1))
        stdout = self._extract_output_section(system_output, "STDOUT")
        stderr = self._extract_output_section(system_output, "STDERR")
        self._log_event(command_logger, {
            **command_data,
            "event_type": "command_executed",
            "message": "Command executed",
            "approval_status": command_data.get("approval_status", "not_required"),
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "level": "INFO" if exit_code == 0 else "ERROR",
            "steps": [f"Command finished with exit code {exit_code}."]
        })
        return system_output

    @staticmethod
    def _extract_output_section(system_output: str, label: str) -> str:
        match = re.search(rf"\[{label}\]:\n(.*?)(?=\n\[(?:STDOUT|STDERR|END-OUTPUT)\]:|\n\[END-OUTPUT\])", system_output, re.DOTALL)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _run_command(command: str) -> str:
        try:
            completed = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
                timeout=int(os.getenv("COMMAND_TIMEOUT_SECONDS", "30")),
                cwd=os.getenv("COMMAND_WORKDIR", "/"),
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout[-12000:]
            stderr = completed.stderr[-12000:]
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = exc.stdout[-12000:] if isinstance(exc.stdout, str) else ""
            stderr = "Command timed out after the configured limit."

        return (
            "[SYSTEM-OUTPUT]\n"
            f"[EXIT_CODE]: {exit_code}\n"
            f"[STDOUT]:\n{stdout}\n"
            f"[STDERR]:\n{stderr}\n"
            "[END-OUTPUT]"
        )


_agent_service = None


def send_agent_message(user_id: int, conversation_id: str, message: str, command_logger=None) -> str:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service.send(user_id, conversation_id, message, command_logger=command_logger)