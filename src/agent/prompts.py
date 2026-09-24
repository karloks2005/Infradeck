import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = Path(os.getenv("PROMPTS_DIR", BASE_DIR)).resolve()


def _resolve_prompt_path(filename: str) -> Path | None:
    candidates = [
        Path(filename),
        PROMPTS_DIR / filename,
        BASE_DIR / filename,
        BASE_DIR / filename.lower(),
        BASE_DIR / filename.upper(),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    for candidate in BASE_DIR.iterdir():
        if candidate.is_file() and candidate.name.lower() == filename.lower():
            return candidate

    return None


def load_prompt(filename: str, default: str = "") -> str:
    path = _resolve_prompt_path(filename)
    if path is None:
        if default:
            return default
        raise FileNotFoundError(f"Prompt file not found: {filename}")
    return path.read_text(encoding="utf-8")


SYSTEM_PROMPT = load_prompt("system_prompt.md")
