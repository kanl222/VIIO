"""Canonical spelling of known search aliases; never changes source documents."""
import re


def normalize_search_text(text: str) -> str:
    text = text.casefold().replace('ё', 'е')
    return re.sub(r'\bис\s*ло\b', 'исло', text)
