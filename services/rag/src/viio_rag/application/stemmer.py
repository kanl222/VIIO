"""Русский стеммер Snowball (py-rust-stemmers, fallback на snowballstemmer) и разбор запроса к графу."""

from __future__ import annotations

import re
from functools import lru_cache

# Стоп-слова записаны через «е» (ё заменяется до проверки)
RUSSIAN_STOP_WORDS: frozenset[str] = frozenset({
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все", "она",
    "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "ее",
    "мне", "было", "вот", "от", "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда",
    "даже", "ну", "вдруг", "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до",
    "вас", "нибудь", "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя", "их", "чем",
    "была", "сам", "чтоб", "без", "будет", "будто", "чего", "эти", "этого", "один",
    "почти", "мой", "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем", "всех",
    "никогда", "можно", "при", "наконец", "два", "об", "другой", "хоть", "после", "над",
    "больше", "тот", "через", "нас", "про", "всего", "них", "какая", "много",
    "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой", "перед", "иногда",
    "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда", "конечно", "всю",
    "между", "подскажи", "покажи", "найти", "какой", "какие", "какое", "кому",
    "такая", "такое", "такие",
})

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

class _PurePythonRussianStemmer:
    """Snowball/Porter algorithm for Russian language in pure Python (zero external dependencies)."""
    PERFECTIVEGROUND = re.compile(r"((ив|ивши|ившись|ыв|ывши|ывшись)|((?<=[ая])(в|вши|вшись)))$")
    REFLEXIVE = re.compile(r"(ся|сь)$")
    ADJECTIVE = re.compile(r"(ее|ие|ые|ое|ими|ыми|ей|ий|ый|ой|ем|им|ым|ом|его|ого|ему|ому|их|ых|ую|юю|ая|яя|ою|ею)$")
    PARTICIPLE = re.compile(r"((ивш|ывш|ующ)|((?<=[ая])(ем|нн|вш|ющ|щ)))$")
    VERB = re.compile(r"((ила|ыла|ена|ейте|уйте|ите|или|ыли|ей|уй|ил|ыл|им|ым|ен|ило|ыло|ено|ят|ует|уют|ит|ыт|ены|ить|ыть|ишь|ую|ю)|((?<=[ая])(ла|на|ете|йте|ли|й|л|ем|н|ло|но|ет|ют|ны|ть|ешь|нно)))$")
    NOUN = re.compile(r"(а|ев|ов|ие|ье|е|иями|ями|ами|еи|ии|и|ией|ей|ой|ий|й|иям|ям|ием|ем|ам|ом|о|у|ах|иях|ях|ы|ь|ию|ью|ю|ия|ья|я)$")
    RVRE = re.compile(r"^(.*?[аеиоуыэюя])(.*)$")
    DERIVATIONAL = re.compile(r".*[^аеиоуыэюя]+[аеиоуыэюя].*ость?$")
    DER = re.compile(r"ость?$")
    SUPERLATIVE = re.compile(r"(ейше|ейш)$")
    I = re.compile(r"и$")
    P = re.compile(r"ь$")
    NN = re.compile(r"нн$")

    def stem(self, word: str) -> str:
        word = word.lower().replace("ё", "е")
        m = self.RVRE.match(word)
        if not m:
            return word
        pre = m.group(1)
        rv = m.group(2)
        if not rv:
            return word

        m_perf = self.PERFECTIVEGROUND.search(rv)
        if m_perf:
            rv = self.PERFECTIVEGROUND.sub("", rv, count=1)
        else:
            rv = self.REFLEXIVE.sub("", rv, count=1)
            m_adj = self.ADJECTIVE.search(rv)
            if m_adj:
                rv = self.ADJECTIVE.sub("", rv, count=1)
                rv = self.PARTICIPLE.sub("", rv, count=1)
            else:
                m_verb = self.VERB.search(rv)
                if m_verb:
                    rv = self.VERB.sub("", rv, count=1)
                else:
                    rv = self.NOUN.sub("", rv, count=1)

        rv = self.I.sub("", rv, count=1)

        if self.DERIVATIONAL.match(rv):
            rv = self.DER.sub("", rv, count=1)

        if self.SUPERLATIVE.search(rv):
            rv = self.SUPERLATIVE.sub("", rv, count=1)
            rv = self.NN.sub("н", rv, count=1)
        else:
            if self.NN.search(rv):
                rv = self.NN.sub("н", rv, count=1)
            else:
                rv = self.P.sub("", rv, count=1)

        return pre + rv


try:
    import py_rust_stemmers
    _rust = py_rust_stemmers.SnowballStemmer("russian")
    _fallback = None
    _py_stemmer = None
except ImportError:
    _rust = None
    try:
        import snowballstemmer
        _fallback = snowballstemmer.stemmer("russian")
        _py_stemmer = None
    except ImportError:
        _fallback = None
        _py_stemmer = _PurePythonRussianStemmer()


@lru_cache(maxsize=50_000)
def stem_word(word: str) -> str:
    """Основа слова (Snowball Russian)."""
    w = word.lower().replace("ё", "е")
    if len(w) < 3:
        return w
    if _rust is not None:
        return _rust.stem_word(w)
    if _fallback is not None:
        return _fallback.stemWord(w)
    return _py_stemmer.stem(w)


# Канонические слова предметных областей
_CONTACT_SEEDS = {
    "телефон", "номер", "контакт", "связь", "позвонить", "дозвониться",
    "горячий", "линия", "справочник", "поддержка", "техподдержка", "колл", "диспетчер",
}
_ORG_SEEDS = {
    "организация", "учреждение", "предприятие", "оас", "госаптека", "склад", "минздрав", "министерство",
}

CONTACT_STEMS: frozenset[str] = frozenset(stem_word(s) for s in _CONTACT_SEEDS)
ORG_STEMS: frozenset[str] = frozenset(stem_word(s) for s in _ORG_SEEDS)


def is_contact_word(word: str) -> bool:
    return stem_word(word) in CONTACT_STEMS


def is_org_word(word: str) -> bool:
    return stem_word(word) in ORG_STEMS


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower().replace("ё", "е"))


def extract_keywords(query: str) -> list[str]:
    """Основы значимых слов запроса: без стоп-слов и дублей, в порядке появления."""
    raw_tokens = _TOKEN_RE.findall(query)
    result: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        clean = token.lower().replace("ё", "е")
        if clean in RUSSIAN_STOP_WORDS or len(clean) < 3:
            continue
        # Аббревиатуры (ИСЛО, МДЛП, WMS, GDP) не стеммим
        stemmed = clean if token.isupper() else stem_word(clean)
        if stemmed not in seen:
            seen.add(stemmed)
            result.append(stemmed)
    return result or [t.lower().replace("ё", "е") for t in raw_tokens]


def normalize_name(name: str) -> str:
    """Нормализация названий узлов и алиасов при индексации графа (хранить в поле name_norm)."""
    return " ".join(stem_word(w) for w in _tokenize(name))


def split_query(query: str) -> tuple[list[str], set[str]]:
    """Возвращает (тематические основы для поиска узлов, найденные намерения)."""
    stems = extract_keywords(query)
    intents: set[str] = set()
    if any(s in CONTACT_STEMS for s in stems):
        intents.add("contact")
    if any(s in ORG_STEMS for s in stems):
        intents.add("org")
    topic = [s for s in stems if s not in CONTACT_STEMS and s not in ORG_STEMS]
    return topic or stems, intents