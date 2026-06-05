"""
Инструмент для brain: точная арифметика без eval() и без LLM.
Подхватывается core.tools как ArithmeticTool.evaluate и т.д.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any, Callable, Dict, List, Union

_BINOPS: Dict[Any, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: Dict[Any, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_round(*args: Any) -> Union[int, float]:
    if len(args) == 1:
        return round(args[0])
    if len(args) == 2:
        return round(args[0], int(args[1]))
    raise ValueError("round ожидает 1 или 2 аргумента")


_SAFE_FUNCS: Dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": _safe_round,
    "min": min,
    "max": max,
    "pow": operator.pow,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "radians": math.radians,
    "degrees": math.degrees,
}

_SAFE_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

_MAX_NODES = 256
_MAX_EXPR_CHARS = 512
_MAX_MULTI_LINES = 48
_MAX_MULTI_TOTAL_CHARS = 8000
# Строка для пакетного режима: только безопасные символы (нет кириллицы, эмодзи, кавычек).
_LINE_SAFE_FOR_MULTI = re.compile(r"^[0-9+\-*/().%\^_,\sa-zA-Z]+$")


def _normalize_expression(raw: str) -> str:
    """
    Поддержка пользовательского математического синтаксиса:
    - '^' трактуем как возведение в степень (аналог '**').
    В Python AST '^' — это XOR, что почти всегда неожиданно для пользователя.
    """
    s = (raw or "").strip()
    if not s:
        return s
    return s.replace("^", "**")


def _as_number(x: Any) -> Union[int, float]:
    if isinstance(x, bool):
        raise ValueError("логические значения не допускаются")
    if isinstance(x, int) and not isinstance(x, bool):
        return x
    if isinstance(x, float):
        if math.isfinite(x):
            return x
        if math.isnan(x):
            raise ValueError("результат не число (NaN)")
        return x
    raise ValueError("ожидалось число")


def _eval_node(node: ast.AST, *, depth: int) -> Union[int, float]:
    if depth > _MAX_NODES:
        raise ValueError("выражение слишком глубокое")
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        raise ValueError("недопустимая константа")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _as_number(_UNARY[type(node.op)](_eval_node(node.operand, depth=depth + 1)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left, depth=depth + 1)
        right = _eval_node(node.right, depth=depth + 1)
        if type(node.op) in (ast.Div, ast.FloorDiv, ast.Mod) and _as_number(right) == 0:
            raise ValueError("деление на ноль")
        return _as_number(_BINOPS[type(node.op)](left, right))
    if isinstance(node, ast.Call):
        if node.keywords:
            raise ValueError("именованные аргументы не поддерживаются")
        if not isinstance(node.func, ast.Name):
            raise ValueError("разрешены только простые вызовы sqrt(), abs(), …")
        fn = _SAFE_FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError(f"неизвестная функция: {node.func.id}")
        args = [_eval_node(a, depth=depth + 1) for a in node.args]
        try:
            out = fn(*args)
        except Exception as e:
            raise ValueError(str(e)) from e
        return _as_number(out)
    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _as_number(_SAFE_NAMES[node.id])
        raise ValueError(f"неизвестный идентификатор: {node.id}")
    raise ValueError("недопустимая конструкция в выражении")


def safe_eval_arithmetic(expression: str) -> Union[int, float]:
    raw = _normalize_expression(expression)
    if not raw:
        raise ValueError("пустое выражение")
    if len(raw) > _MAX_EXPR_CHARS:
        raise ValueError("выражение слишком длинное")
    try:
        tree = ast.parse(raw, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"синтаксис: {e}") from e
    return _eval_node(tree.body, depth=0)


def _line_allowed_for_multi(s: str) -> bool:
    t = (s or "").strip()
    if not t or len(t) > _MAX_EXPR_CHARS:
        return False
    return bool(_LINE_SAFE_FOR_MULTI.match(t))


def _split_expression_chunks(raw: str) -> List[str]:
    return [p.strip() for p in re.split(r"[\n;]+", (raw or "").strip()) if p.strip()]


class ArithmeticToolModule:
    """Публичные методы становятся инструментами brain (ArithmeticTool.*)."""

    def evaluate(self, expression: str = "") -> Dict[str, Any]:
        """
        Точно вычислить арифметическое выражение (латиница, цифры, операторы).
        Поддержка: + - * / // % **, скобки, sqrt, abs, floor, ceil, round, min, max, pow,
        log, log10, exp, sin, cos, tan, radians, degrees; константы pi, e, tau.

        Несколько выражений: разделитель перевод строки или `;`. Строки с текстом/эмодзи
        (не только допустимые символы) пропускаются — считаются только «чистые» формулы.
        При успехе нескольких: ok=true, multi=true, results=[{expression, result}, …];
        при одном как раньше: result=число.
        """
        expr_full = (expression or "").strip()
        if not expr_full:
            return {
                "ok": False,
                "error": "expression required",
                "hint": "Передай выражение в args.expression (или expr / formula).",
            }
        try:
            val = safe_eval_arithmetic(expr_full)
            return {
                "ok": True,
                "result": val,
                "expression": expr_full,
            }
        except Exception as e:
            single_err = str(e)

        chunks = _split_expression_chunks(expr_full)
        if len(chunks) <= 1:
            return {
                "ok": False,
                "error": single_err,
                "expression": expr_full,
            }

        eval_lines = [c for c in chunks if _line_allowed_for_multi(c)]
        if not eval_lines:
            return {
                "ok": False,
                "error": (
                    f"{single_err} — для пакетного режима нужны отдельные строки (или блоки через `;`) "
                    "только из цифр, латиницы и операторов; строки с описанием товара пропускаются."
                ),
                "expression": expr_full,
            }
        if len(eval_lines) > _MAX_MULTI_LINES:
            return {
                "ok": False,
                "error": f"слишком много выражений ({len(eval_lines)}), максимум {_MAX_MULTI_LINES}",
                "expression": expr_full,
            }
        total_len = sum(len(x) for x in eval_lines)
        if total_len > _MAX_MULTI_TOTAL_CHARS:
            return {
                "ok": False,
                "error": "суммарная длина выражений слишком большая",
                "expression": expr_full,
            }

        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for line in eval_lines:
            try:
                results.append(
                    {
                        "expression": line,
                        "result": safe_eval_arithmetic(line),
                    }
                )
            except Exception as ex:
                errors.append({"expression": line, "error": str(ex)})

        if not results:
            return {
                "ok": False,
                "error": errors[0].get("error", single_err) if errors else single_err,
                "expression": expr_full,
                "errors": errors,
            }

        out: Dict[str, Any] = {
            "ok": True,
            "multi": True,
            "results": results,
            "expression": expr_full,
            "evaluated_count": len(results),
        }
        if errors:
            out["errors"] = errors
        if len(results) == 1 and not errors:
            out["result"] = results[0]["result"]
            del out["multi"]
            out.pop("results", None)
            out.pop("evaluated_count", None)
        return out
