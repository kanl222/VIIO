"""Small, explicit routing rules shared by the supervisor and model graphs."""

from __future__ import annotations

import ast
import operator
import re
from decimal import Decimal, DecimalException, localcontext

NO_CODE = re.compile(
    r"код\s+(?:писать\s+)?не\s+(?:нуж\w*|надо)|не\s+(?:пиши\w*|писать)\s+код|"
    r"без\s+(?:написания\s+)?кода", re.I,
)
DOMAIN = re.compile(
    r"баз[аеуы]\s+знани|документ|регламент|инструкци|наш(?:ей|ем|его|их)?\s+"
    r"(?:систем|проект|организац)|ис\s*[«\"]?ло|ssrs|availablereports|\bviio\b", re.I,
)
REWRITE = re.compile(r"^\s*(?:(?:перепиши|переформулируй|отредактируй|сократи|переведи)\b|сделай\s+(?:текст\s+)?(?:вежлив|короче|официальн))", re.I)
FOLLOWUP = re.compile(
    r"^\s*(?:добавь|исправь|измени|убери|теперь|продолжи|учти|сделай|верни|"
    r"объясни\s+(?:этот|предыдущий)|а\s+(?:если|теперь))\b", re.I,
)


def request_mode(message: str) -> str:
    """Default to retrieval for ambiguous business/domain questions."""
    # A quoted instruction to rewrite is different from rewriting a function.
    instruction = message.split(":", 1)[0]
    if REWRITE.search(message) and not re.search(
        r"\b(?:python|sql|soap|rest|api|функци\w*|код|метод\w*)\b", instruction, re.I,
    ) and not DOMAIN.search(instruction):
        return "rewrite"
    if DOMAIN.search(message):
        return "knowledge"
    if re.fullmatch(r"\s*(?:привет|здравствуй(?:те)?|доброе утро|добрый (?:день|вечер)|"
                    r"спасибо(?: большое)?|благодарю|пока|до свидания|hello|hi|thanks|thank you)[\s!.,?]*", message, re.I):
        return "general"
    if re.search(r"^\s*(?:почему|как|что такое)\s+.*(?:земл|солнц|планет|фотосинтез|гравитац)", message, re.I):
        return "general"
    if re.match(r"\s*(?:объясни|расскажи|почему|что такое)\b", message, re.I) and not re.search(
        r"компани|организац|отпуск|отч[её]т|сотрудник|лиценз|рецепт|систем|доступ|уч[её]тн|правил|процедур", message, re.I,
    ):
        return "general"
    if NO_CODE.search(message):
        return "general"
    return "knowledge"


def arithmetic_expression(message: str) -> str | None:
    """Accept only a whole expression plus a small set of explicit wrappers."""
    text = message.strip()
    explicit = bool(re.match(r"^(?:вычисли|посчитай|сколько будет|calculate)\s+", text, re.I))
    text = re.sub(r"^(?:вычисли|посчитай|сколько будет|calculate)\s+", "", text, flags=re.I)
    text = re.sub(r"[.!?]\s*(?:дай|ответь|выведи)\s+только\s+(?:число|результат)\s*[.!?]*$", "", text, flags=re.I)
    text = text.rstrip(" .?!").replace("×", "*").replace("÷", "/").replace("−", "-")
    if not re.fullmatch(r"[\d\s.+*/()%\-]+", text) or not re.search(r"\d", text):
        return None
    if not explicit and not re.search(r"[+*/%]|\d\s*-\s*\d", text):
        return None
    return text


def calculate(message: str) -> str | None:
    """Bounded decimal arithmetic, no eval, calls, names, or exponentiation."""
    expression = arithmetic_expression(message)
    if expression is None:
        return None
    try:
        tree = _arithmetic_tree(expression)
        with localcontext() as context:
            context.prec = 50
            value = _decimal_value(tree.body, expression)
        return (format(value, "f").rstrip("0").rstrip(".") if value != value.to_integral() else str(int(value))) or "0"
    except (ValueError, SyntaxError, DecimalException, RecursionError):
        return "Не удалось вычислить выражение. Поддерживаются числа, скобки и операции +, -, *, /, %. Проверьте размер чисел и деление на ноль."


def _arithmetic_tree(expression: str):
    if len(expression) > 256 or "**" in expression or "//" in expression:
        raise ValueError("Операция не поддерживается")
    nesting = 0
    for char in expression:
        nesting += (char == "(") - (char == ")")
        if nesting > 16:
            raise ValueError("Слишком глубокое выражение")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ValueError("Слишком длинное выражение")
    return tree


def _decimal_value(node, expression: str, depth: int = 0):
    if depth > 16:
        raise ValueError("Слишком глубокое выражение")
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Mod: operator.mod}
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        value = Decimal(ast.get_source_segment(expression, node))
        if len(value.as_tuple().digits) > 40:
            raise ValueError("Слишком большое число")
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _decimal_value(node.operand, expression, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
    elif isinstance(node, ast.BinOp) and type(node.op) in operations:
        value = operations[type(node.op)](_decimal_value(node.left, expression, depth + 1), _decimal_value(node.right, expression, depth + 1))
    else:
        raise ValueError("Операция не поддерживается")
    if not value.is_finite() or abs(value) > Decimal("1e40"):
        raise ValueError("Слишком большой результат")
    return value
