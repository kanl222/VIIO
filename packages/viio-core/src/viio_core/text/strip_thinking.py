"""Text processing and reasoning tag stripping."""
import re

THINKING_PATTERN = re.compile(r"(?is)<(?:think|thought|reasoning)>.*?</(?:think|thought|reasoning)>\s*")
TRAILING_THINKING_PATTERN = re.compile(r"(?is)<(?:think|thought|reasoning)>.*$")


def strip_thinking(text: str) -> str:
    """Strip hidden reasoning tags (<think>...</think>) from LLM outputs."""
    if not text:
        return ""
    cleaned = THINKING_PATTERN.sub("", text)
    cleaned = TRAILING_THINKING_PATTERN.sub("", cleaned)
    return cleaned.strip()
