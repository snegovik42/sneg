#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sneg — маленький встраиваемый язык программирования.

Идея: синтаксис как у Python, но без отступов-ловушек; модель данных как у Lua
(таблицы, функции-значения, замыкания); встраивается в Python одной строкой.

Запуск:
    sneg программа.sneg             # выполнить файл
    sneg                            # REPL
    sneg --check файл               # только проверить синтаксис
    sneg --compile файл             # собрать .snegc (аналог .pyc)
    sneg программа.snegc            # запустить скомпилированное
    sneg --tokens файл              # показать поток токенов
    sneg --ast файл                 # показать дерево разбора

Устройство файла (сверху вниз — как работает любой интерпретатор):
    1. Лексер      — режет текст на токены
    2. Парсер      — собирает из токенов дерево (AST)
    3. Окружение   — таблица имён со ссылкой на внешнее окружение
    4. Интерпретатор — обходит дерево и выполняет
"""

import json
import math
import os
import random
import sys
import time

VERSION = "0.1.0"


# ===========================================================================
#  ОШИБКИ
# ===========================================================================

class SnegError(Exception):
    """Ошибка языка: сообщение + место, где случилось."""

    def __init__(self, message, line=None, col=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.frames = []          # стек вызовов, набирается по ходу всплытия

    def with_frame(self, frame):
        self.frames.append(frame)
        return self


def format_error(err, lines, filename="<программа>"):
    """Текст ошибки с подсветкой строки и каретки под проблемным местом."""
    out = [f"\n\u274c Ошибка в {filename}: {err.message}"]

    if err.line is not None and 0 < err.line <= len(lines):
        src = lines[err.line - 1].rstrip("\n")
        out.append(f"\n  строка {err.line}")
        out.append(f"    {src}")

        if err.col is not None and err.col > 0:
            # приводим колонку к отображаемой ширине (табы считаем за 4)
            shown = src[: max(0, err.col - 1)].replace("\t", "    ")
            out.append("    " + " " * len(shown) + "^")

    if err.frames:
        out.append("\n  стек вызовов:")
        for frame in err.frames[-6:]:
            out.append("    " + frame)

    return "\n".join(out)


# ===========================================================================
#  1. ЛЕКСЕР
# ===========================================================================

class Token:
    __slots__ = ("kind", "value", "line", "col")

    def __init__(self, kind, value, line, col):
        self.kind = kind      # NUM / STR / IDENT / KW / OP / EOF
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.kind}, {self.value!r}, {self.line}:{self.col})"


# Ключевые слова. Слева — как пишется, справа — внутреннее имя.
# Русские варианты не обязательны, но на них приятнее писать.
KEYWORDS = {
    "let": "let", "пусть": "let",
    "fn": "fn", "функция": "fn",
    "if": "if", "если": "if",
    "elseif": "elseif", "иначе_если": "elseif",
    "else": "else", "иначе": "else",
    "while": "while", "пока": "while",
    "for": "for", "для": "for",
    "in": "in", "в": "in",
    "return": "return", "вернуть": "return",
    "break": "break", "прервать": "break",
    "continue": "continue", "продолжить": "continue",
    "true": "true", "истина": "true",
    "false": "false", "ложь": "false",
    "nil": "nil", "ничего": "nil",
    "and": "and", "и": "and",
    "or": "or", "или": "or",
    "not": "not", "не": "not",
}

# Длинные операторы должны проверяться первыми.
OPERATORS = [
    "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "&&", "||",
    "=", "<", ">", "+", "-", "*", "/", "%",
    "(", ")", "{", "}", "[", "]", ",", ".", ":", ";", "!",
]

ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "0": "\0",
    '"': '"', "'": "'", "\\": "\\",
}


class Lexer:
    def __init__(self, source, filename="<программа>"):
        self.src = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens = []

    # --- мелкие помощники ------------------------------------------------
    def peek(self, offset=0):
        idx = self.pos + offset
        return self.src[idx] if idx < len(self.src) else ""

    def advance(self, count=1):
        for _ in range(count):
            if self.pos < len(self.src):
                if self.src[self.pos] == "\n":
                    self.line += 1
                    self.col = 1
                else:
                    self.col += 1
                self.pos += 1

    def error(self, message):
        raise SnegError(message, self.line, self.col)

    # --- основной цикл ---------------------------------------------------
    def tokenize(self):
        while self.pos < len(self.src):
            ch = self.peek()

            if ch in " \t\r\n":
                self.advance()
                continue

            if ch == "#" or (ch == "/" and self.peek(1) == "/"):
                while self.pos < len(self.src) and self.peek() != "\n":
                    self.advance()
                continue

            if ch == "/" and self.peek(1) == "*":
                self.advance(2)
                while self.pos < len(self.src) and not (self.peek() == "*" and self.peek(1) == "/"):
                    self.advance()
                if self.pos >= len(self.src):
                    self.error("незакрытый комментарий /* ... */")
                self.advance(2)
                continue

            if ch.isdigit():
                self.read_number()
                continue

            if ch in "\"'":
                self.read_string()
                continue

            if ch.isalpha() or ch == "_":
                self.read_identifier()
                continue

            self.read_operator()

        self.tokens.append(Token("EOF", "", self.line, self.col))
        return self.tokens

    def read_number(self):
        start_line, start_col = self.line, self.col
        text = ""
        seen_dot = False

        while self.pos < len(self.src):
            ch = self.peek()
            if ch.isdigit() or ch == "_":
                text += ch
                self.advance()
            elif ch == "." and not seen_dot and self.peek(1).isdigit():
                seen_dot = True
                text += ch
                self.advance()
            else:
                break

        text = text.replace("_", "")
        self.tokens.append(Token("NUM", float(text), start_line, start_col))

    def read_string(self):
        quote = self.peek()
        start_line, start_col = self.line, self.col
        self.advance()

        chars = []
        while True:
            if self.pos >= len(self.src):
                raise SnegError("незакрытая строка", start_line, start_col)

            ch = self.peek()

            if ch == "\\":
                self.advance()
                esc = self.peek()
                if esc == "":
                    raise SnegError("незакрытая строка", start_line, start_col)
                chars.append(ESCAPES.get(esc, esc))
                self.advance()
                continue

            if ch == quote:
                self.advance()
                break

            if ch == "\n":
                raise SnegError("строка не может переноситься на новую строку",
                                self.line, self.col)

            chars.append(ch)
            self.advance()

        self.tokens.append(Token("STR", "".join(chars), start_line, start_col))

    def read_identifier(self):
        start_line, start_col = self.line, self.col
        chars = []

        while self.pos < len(self.src):
            ch = self.peek()
            if ch.isalnum() or ch == "_":
                chars.append(ch)
                self.advance()
            else:
                break

        word = "".join(chars)
        kind = "KW" if word in KEYWORDS else "IDENT"
        value = KEYWORDS[word] if kind == "KW" else word
        self.tokens.append(Token(kind, value, start_line, start_col))

    def read_operator(self):
        start_line, start_col = self.line, self.col

        for op in OPERATORS:
            if self.src.startswith(op, self.pos):
                self.advance(len(op))
                # && и || — просто удобные псевдонимы
                canonical = {"&&": "and", "||": "or"}.get(op, op)
                self.tokens.append(Token("OP", canonical, start_line, start_col))
                return

        bad = self.peek()
        self.error(f"непонятный символ {bad!r}")


# ===========================================================================
#  2. ДЕРЕВО РАЗБОРА (AST)
# ===========================================================================

class Node:
    def __init__(self, line=0, col=0):
        self.line = line
        self.col = col


# --- выражения -------------------------------------------------------------
class Literal(Node):
    def __init__(self, value, line=0, col=0):
        super().__init__(line, col)
        self.value = value


class Var(Node):
    def __init__(self, name, line=0, col=0):
        super().__init__(line, col)
        self.name = name


class ListLit(Node):
    def __init__(self, items, line=0, col=0):
        super().__init__(line, col)
        self.items = items


class MapLit(Node):
    def __init__(self, pairs, line=0, col=0):
        super().__init__(line, col)
        self.pairs = pairs          # [(key_node, value_node), ...]


class FuncLit(Node):
    def __init__(self, params, body, name="<аноним>", line=0, col=0):
        super().__init__(line, col)
        self.params = params
        self.body = body
        self.name = name


class Unary(Node):
    def __init__(self, op, operand, line=0, col=0):
        super().__init__(line, col)
        self.op = op
        self.operand = operand


class Binary(Node):
    def __init__(self, op, left, right, line=0, col=0):
        super().__init__(line, col)
        self.op = op
        self.left = left
        self.right = right


class Logical(Node):
    def __init__(self, op, left, right, line=0, col=0):
        super().__init__(line, col)
        self.op = op
        self.left = left
        self.right = right


class Get(Node):
    """a.b — доступ к полю таблицы."""
    def __init__(self, obj, name, line=0, col=0):
        super().__init__(line, col)
        self.obj = obj
        self.name = name


class Index(Node):
    """a[i]"""
    def __init__(self, obj, index, line=0, col=0):
        super().__init__(line, col)
        self.obj = obj
        self.index = index


class Call(Node):
    def __init__(self, callee, args, line=0, col=0):
        super().__init__(line, col)
        self.callee = callee
        self.args = args


# --- операторы -------------------------------------------------------------
class Let(Node):
    def __init__(self, name, value, line=0, col=0):
        super().__init__(line, col)
        self.name = name
        self.value = value


class Assign(Node):
    def __init__(self, target, op, value, line=0, col=0):
        super().__init__(line, col)
        self.target = target
        self.op = op                # "=", "+=", "-=", "*=", "/="
        self.value = value


class ExprStmt(Node):
    def __init__(self, expr, line=0, col=0):
        super().__init__(line, col)
        self.expr = expr


class Block(Node):
    def __init__(self, statements, line=0, col=0):
        super().__init__(line, col)
        self.statements = statements


class If(Node):
    def __init__(self, branches, else_body, line=0, col=0):
        super().__init__(line, col)
        self.branches = branches    # [(cond, block), ...]
        self.else_body = else_body  # block или None


class While(Node):
    def __init__(self, cond, body, line=0, col=0):
        super().__init__(line, col)
        self.cond = cond
        self.body = body


class ForIn(Node):
    def __init__(self, name, iterable, body, line=0, col=0):
        super().__init__(line, col)
        self.name = name
        self.iterable = iterable
        self.body = body


class Return(Node):
    def __init__(self, value, line=0, col=0):
        super().__init__(line, col)
        self.value = value


class Break(Node):
    pass


class Continue(Node):
    pass


class FuncDef(Node):
    def __init__(self, name, func, line=0, col=0):
        super().__init__(line, col)
        self.name = name
        self.func = func


# ===========================================================================
#  3. ПАРСЕР
# ===========================================================================

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    # --- помощники -------------------------------------------------------
    def current(self):
        return self.tokens[self.pos]

    def check(self, kind, value=None):
        tok = self.current()
        if tok.kind != kind:
            return False
        return value is None or tok.value == value

    def check_op(self, *values):
        tok = self.current()
        return tok.kind == "OP" and tok.value in values

    def check_kw(self, *values):
        tok = self.current()
        return tok.kind == "KW" and tok.value in values

    def advance(self):
        tok = self.current()
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def expect(self, kind, value=None, message=None):
        if self.check(kind, value):
            return self.advance()

        tok = self.current()
        found = "конец файла" if tok.kind == "EOF" else repr(tok.value)
        raise SnegError(
            message or f"ожидалось {value!r}, а получено {found}",
            tok.line, tok.col)

    def expect_op(self, value, message=None):
        return self.expect("OP", value, message)

    def skip_semis(self):
        while self.check_op(";"):
            self.advance()

    # --- вход ------------------------------------------------------------
    def parse(self):
        statements = []
        while not self.check("EOF"):
            self.skip_semis()
            if self.check("EOF"):
                break
            statements.append(self.statement())
        return statements

    # --- операторы -------------------------------------------------------
    def statement(self):
        tok = self.current()

        if tok.kind == "KW":
            if tok.value == "let":
                return self.let_statement()
            if tok.value == "fn":
                # fn имя(...) { } — объявление, если дальше идёт имя
                if self.tokens[self.pos + 1].kind == "IDENT":
                    return self.fn_statement()
            if tok.value == "if":
                return self.if_statement()
            if tok.value == "while":
                return self.while_statement()
            if tok.value == "for":
                return self.for_statement()
            if tok.value == "return":
                return self.return_statement()
            if tok.value == "break":
                self.advance()
                return Break(tok.line, tok.col)
            if tok.value == "continue":
                self.advance()
                return Continue(tok.line, tok.col)

        # выражение или присваивание
        expr = self.expression()

        if self.check_op("=", "+=", "-=", "*=", "/="):
            op = self.advance().value
            if not isinstance(expr, (Var, Get, Index)):
                raise SnegError("слева от знака присваивания должна быть переменная "
                                "или поле таблицы", expr.line, expr.col)
            value = self.expression()
            return Assign(expr, op, value, expr.line, expr.col)

        return ExprStmt(expr, expr.line, expr.col)

    def let_statement(self):
        tok = self.advance()                      # let
        name_tok = self.expect("IDENT", message="после let нужно имя переменной")
        self.expect_op("=", "после имени переменной нужен знак =")
        value = self.expression()
        return Let(name_tok.value, value, tok.line, tok.col)

    def fn_statement(self):
        tok = self.advance()                      # fn
        name_tok = self.expect("IDENT", message="после fn нужно имя функции")
        params = self.params()
        body = self.block()

        if body is None:
            raise SnegError("после параметров функции нужен блок { ... }",
                            tok.line, tok.col)

        func = FuncLit(params, body, name_tok.value, tok.line, tok.col)
        return FuncDef(name_tok.value, func, tok.line, tok.col)

    def params(self):
        self.expect_op("(", "после имени функции нужна скобка (")
        params = []

        if not self.check_op(")"):
            while True:
                tok = self.expect("IDENT", message="ожидалось имя параметра")
                params.append(tok.value)
                if self.check_op(","):
                    self.advance()
                    continue
                break

        self.expect_op(")", "не закрыта скобка в списке параметров")
        return params

    def if_statement(self):
        tok = self.advance()                      # if
        branches = []

        cond = self.expression()
        body = self.block()
        if body is None:
            raise SnegError("после условия нужен блок { ... }", tok.line, tok.col)
        branches.append((cond, body))

        while self.check_kw("elseif"):
            self.advance()
            cond = self.expression()
            body = self.block()
            if body is None:
                raise SnegError("после условия нужен блок { ... }", tok.line, tok.col)
            branches.append((cond, body))

        else_body = None
        if self.check_kw("else"):
            self.advance()
            else_body = self.block()
            if else_body is None:
                raise SnegError("после else нужен блок { ... }", tok.line, tok.col)

        return If(branches, else_body, tok.line, tok.col)

    def while_statement(self):
        tok = self.advance()                      # while
        cond = self.expression()
        body = self.block()
        if body is None:
            raise SnegError("после условия цикла нужен блок { ... }", tok.line, tok.col)
        return While(cond, body, tok.line, tok.col)

    def for_statement(self):
        tok = self.advance()                      # for
        name_tok = self.expect("IDENT", message="после for нужно имя переменной")
        self.expect("KW", "in", "в цикле for нужно слово in: for x in список")
        iterable = self.expression()
        body = self.block()
        if body is None:
            raise SnegError("после заголовка цикла нужен блок { ... }", tok.line, tok.col)
        return ForIn(name_tok.value, iterable, body, tok.line, tok.col)

    def return_statement(self):
        tok = self.advance()                      # return
        if self.starts_expression():
            value = self.expression()
        else:
            value = Literal(None, tok.line, tok.col)
        return Return(value, tok.line, tok.col)

    def block(self):
        if not self.check_op("{"):
            return None
        open_tok = self.advance()

        statements = []
        while not self.check_op("}"):
            if self.check("EOF"):
                raise SnegError("блок не закрыт: не хватает }",
                                open_tok.line, open_tok.col)
            self.skip_semis()
            if self.check_op("}"):
                break
            statements.append(self.statement())

        self.expect_op("}", "блок не закрыт: не хватает }")
        return Block(statements, open_tok.line, open_tok.col)

    def starts_expression(self):
        tok = self.current()
        if tok.kind in ("NUM", "STR", "IDENT"):
            return True
        if tok.kind == "KW" and tok.value in ("fn", "true", "false", "nil", "not"):
            return True
        return tok.kind == "OP" and tok.value in ("(", "[", "{", "-", "!")

    # --- выражения -------------------------------------------------------
    def expression(self):
        return self.or_expr()

    def or_expr(self):
        left = self.and_expr()
        while self.check_kw("or"):
            tok = self.advance()
            right = self.and_expr()
            left = Logical("or", left, right, tok.line, tok.col)
        return left

    def and_expr(self):
        left = self.equality()
        while self.check_kw("and"):
            tok = self.advance()
            right = self.equality()
            left = Logical("and", left, right, tok.line, tok.col)
        return left

    def equality(self):
        left = self.comparison()
        while self.check_op("==", "!="):
            tok = self.advance()
            right = self.comparison()
            left = Binary(tok.value, left, right, tok.line, tok.col)
        return left

    def comparison(self):
        left = self.term()
        while self.check_op("<", "<=", ">", ">=") or self.check_kw("in"):
            tok = self.advance()
            right = self.term()
            left = Binary(tok.value, left, right, tok.line, tok.col)
        return left

    def term(self):
        left = self.factor()
        while self.check_op("+", "-"):
            tok = self.advance()
            right = self.factor()
            left = Binary(tok.value, left, right, tok.line, tok.col)
        return left

    def factor(self):
        left = self.unary()
        while self.check_op("*", "/", "%"):
            tok = self.advance()
            right = self.unary()
            left = Binary(tok.value, left, right, tok.line, tok.col)
        return left

    def unary(self):
        if self.check_op("-", "!"):
            tok = self.advance()
            op = "not" if tok.value == "!" else "-"
            return Unary(op, self.unary(), tok.line, tok.col)
        if self.check_kw("not"):
            tok = self.advance()
            return Unary("not", self.unary(), tok.line, tok.col)
        return self.postfix()

    def postfix(self):
        expr = self.primary()

        while True:
            if self.check_op("("):
                tok = self.advance()
                args = []
                if not self.check_op(")"):
                    while True:
                        args.append(self.expression())
                        if self.check_op(","):
                            self.advance()
                            continue
                        break
                self.expect_op(")", "не закрыта скобка в вызове функции")
                expr = Call(expr, args, tok.line, tok.col)

            elif self.check_op("."):
                self.advance()
                name_tok = self.expect("IDENT", message="после точки нужно имя поля")
                expr = Get(expr, name_tok.value, name_tok.line, name_tok.col)

            elif self.check_op("["):
                tok = self.advance()
                index = self.expression()
                self.expect_op("]", "не закрыта скобка в индексе")
                expr = Index(expr, index, tok.line, tok.col)

            else:
                return expr

    def primary(self):
        tok = self.current()

        if tok.kind == "NUM":
            self.advance()
            return Literal(tok.value, tok.line, tok.col)

        if tok.kind == "STR":
            self.advance()
            return Literal(tok.value, tok.line, tok.col)

        if tok.kind == "KW":
            if tok.value in ("true", "false"):
                self.advance()
                return Literal(tok.value == "true", tok.line, tok.col)
            if tok.value == "nil":
                self.advance()
                return Literal(None, tok.line, tok.col)
            if tok.value == "fn":
                self.advance()
                params = self.params()
                body = self.block()
                if body is None:
                    raise SnegError("у функции должен быть блок { ... }",
                                    tok.line, tok.col)
                return FuncLit(params, body, "<аноним>", tok.line, tok.col)

        if tok.kind == "IDENT":
            self.advance()
            return Var(tok.value, tok.line, tok.col)

        if tok.kind == "OP" and tok.value == "(":
            self.advance()
            expr = self.expression()
            self.expect_op(")", "не закрыта скобка")
            return expr

        if tok.kind == "OP" and tok.value == "[":
            return self.list_literal()

        if tok.kind == "OP" and tok.value == "{":
            return self.map_literal()

        found = "конец файла" if tok.kind == "EOF" else repr(tok.value)
        raise SnegError(f"ожидалось выражение, а получено {found}", tok.line, tok.col)

    def list_literal(self):
        tok = self.advance()                      # [
        items = []

        while not self.check_op("]"):
            if self.check("EOF"):
                raise SnegError("список не закрыт: не хватает ]", tok.line, tok.col)
            items.append(self.expression())
            if self.check_op(","):
                self.advance()
                continue
            break

        self.expect_op("]", "список не закрыт: не хватает ]")
        return ListLit(items, tok.line, tok.col)

    def map_literal(self):
        tok = self.advance()                      # {
        pairs = []

        while not self.check_op("}"):
            if self.check("EOF"):
                raise SnegError("таблица не закрыта: не хватает }", tok.line, tok.col)

            key_tok = self.current()
            if key_tok.kind in ("IDENT", "STR"):
                self.advance()
                key = Literal(key_tok.value, key_tok.line, key_tok.col)
            else:
                key = self.expression()

            self.expect_op(":", "в таблице между ключом и значением нужно :")
            value = self.expression()
            pairs.append((key, value))

            if self.check_op(","):
                self.advance()
                continue
            break

        self.expect_op("}", "таблица не закрыта: не хватает }")
        return MapLit(pairs, tok.line, tok.col)


# ===========================================================================
#  4. ОКРУЖЕНИЕ (области видимости)
# ===========================================================================

class Environment:
    def __init__(self, parent=None, name="<глобальная>"):
        self.values = {}
        self.parent = parent
        self.name = name

    def define(self, name, value):
        self.values[name] = value

    def get(self, name, line=None, col=None):
        env = self
        while env is not None:
            if name in env.values:
                return env.values[name]
            env = env.parent
        raise SnegError(f"переменная {name!r} не объявлена", line, col)

    def set(self, name, value, line=None, col=None):
        env = self
        while env is not None:
            if name in env.values:
                env.values[name] = value
                return value
            env = env.parent
        raise SnegError(f"переменная {name!r} не объявлена — сначала объяви её через let",
                        line, col)


# ===========================================================================
#  5. ЗНАЧЕНИЯ
# ===========================================================================

class SnegFunction:
    """Функция, написанная на sneg: параметры, тело и окружение создания."""
    def __init__(self, params, body, closure, name="<аноним>"):
        self.params = params
        self.body = body
        self.closure = closure
        self.name = name

    def __repr__(self):
        return f"<функция {self.name}>"


class Builtin:
    """Функция, написанная на Python и отданная в язык."""
    def __init__(self, name, func):
        self.name = name
        self.func = func

    def __repr__(self):
        return f"<встроенная {self.name}>"


class SnegRange:
    """Диапазон: не занимает память, как список из миллиона чисел."""
    def __init__(self, start, stop, step=1.0):
        self.start = start
        self.stop = stop
        self.step = step

    def __iter__(self):
        value = self.start
        if self.step > 0:
            while value < self.stop:
                yield value
                value += self.step
        else:
            while value > self.stop:
                yield value
                value += self.step

    def __len__(self):
        if self.step == 0:
            return 0
        if self.step > 0:
            return max(0, int(math.ceil((self.stop - self.start) / self.step)))
        return max(0, int(math.ceil((self.start - self.stop) / -self.step)))

    def contains(self, value):
        for item in self:
            if item == value:
                return True
        return False

    def __repr__(self):
        return f"<диапазон {self.start:g}..{self.stop:g}>"


def is_number(value):
    return isinstance(value, float)


def is_truthy(value):
    """Ложны только nil и false — как в Lua. Ноль и пустая строка истинны."""
    return not (value is None or value is False)


def fmt_number(value):
    if value != value:            # NaN
        return "nan"
    if value in (float("inf"), float("-inf")):
        return "inf" if value > 0 else "-inf"
    if value.is_integer() and abs(value) < 1e16:
        return str(int(value))
    text = repr(value)
    return text


def type_name(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "bool"
    if is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    if isinstance(value, SnegRange):
        return "range"
    if isinstance(value, (SnegFunction, Builtin)):
        return "function"
    return "unknown"


def to_string(value, depth=0):
    """Представление значения для print и конкатенации."""
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if is_number(value):
        return fmt_number(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (SnegFunction, Builtin)):
        return repr(value)
    if isinstance(value, SnegRange):
        return repr(value)

    if depth > 4:
        return "..."

    if isinstance(value, list):
        return "[" + ", ".join(quote_if_string(v, depth + 1) for v in value) + "]"

    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(f"{key}: {quote_if_string(item, depth + 1)}")
        return "{" + ", ".join(parts) + "}"

    return str(value)


def quote_if_string(value, depth):
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return to_string(value, depth)


def values_equal(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_number(a) and is_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(values_equal(a[k], b[k]) for k in a)
    return a is b


# ===========================================================================
#  6. СИГНАЛЫ УПРАВЛЕНИЯ
# ===========================================================================

class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


# ===========================================================================
#  7. ИНТЕРПРЕТАТОР
# ===========================================================================

class Interpreter:
    def __init__(self, out=None, stdin=None):
        self.out = out or sys.stdout
        self.stdin = stdin or sys.stdin
        self.globals = Environment(name="<глобальная>")
        self.install_builtins()

    # --- вывод -----------------------------------------------------------
    def write(self, text):
        self.out.write(text)
        self.out.flush()

    # --- точка входа ------------------------------------------------------
    def execute(self, statements, env=None):
        env = env or self.globals
        for stmt in statements:
            self.exec_stmt(stmt, env)
        return env

    # --- операторы --------------------------------------------------------
    def exec_stmt(self, node, env):
        if isinstance(node, Let):
            value = self.eval(node.value, env)
            if node.name in env.values:
                raise SnegError(
                    f"переменная {node.name!r} уже объявлена в этой области видимости",
                    node.line, node.col)
            env.define(node.name, value)
            return

        if isinstance(node, Assign):
            self.exec_assign(node, env)
            return

        if isinstance(node, ExprStmt):
            self.eval(node.expr, env)
            return

        if isinstance(node, FuncDef):
            # замыкание — то окружение, где функция была объявлена
            env.define(
                node.name,
                SnegFunction(node.func.params, node.func.body, env, node.name))
            return

        if isinstance(node, If):
            for cond, body in node.branches:
                if is_truthy(self.eval(cond, env)):
                    self.exec_block(body, Environment(env, "блок if"))
                    return
            if node.else_body is not None:
                self.exec_block(node.else_body, Environment(env, "блок else"))
            return

        if isinstance(node, While):
            while is_truthy(self.eval(node.cond, env)):
                try:
                    self.exec_block(node.body, Environment(env, "тело while"))
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
            return

        if isinstance(node, ForIn):
            for item in self.iterate(self.eval(node.iterable, env), node):
                scope = Environment(env, "тело for")
                scope.define(node.name, item)
                try:
                    self.exec_block(node.body, scope)
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
            return

        if isinstance(node, Return):
            raise ReturnSignal(self.eval(node.value, env))

        if isinstance(node, Break):
            raise BreakSignal()

        if isinstance(node, Continue):
            raise ContinueSignal()

        if isinstance(node, Block):
            self.exec_block(node, Environment(env, "блок"))
            return

        raise SnegError(f"неизвестный оператор {type(node).__name__}",
                        node.line, node.col)

    def exec_block(self, block, env):
        for stmt in block.statements:
            self.exec_stmt(stmt, env)

    def exec_assign(self, node, env):
        target = node.target

        if node.op == "=":
            value = self.eval(node.value, env)
        else:
            current = self.eval(target, env)
            right = self.eval(node.value, env)
            value = self.binary_op(node.op[0], current, right, node)

        if isinstance(target, Var):
            env.set(target.name, value, node.line, node.col)
            return

        if isinstance(target, Get):
            obj = self.eval(target.obj, env)
            if not isinstance(obj, dict):
                raise SnegError(
                    f"нельзя записать поле в {type_name(obj)} — это не таблица",
                    node.line, node.col)
            obj[target.name] = value
            return

        if isinstance(target, Index):
            obj = self.eval(target.obj, env)
            index = self.eval(target.index, env)
            self.set_index(obj, index, value, node)
            return

        raise SnegError("некуда присваивать", node.line, node.col)

    # --- выражения --------------------------------------------------------
    def eval(self, node, env):
        if isinstance(node, Literal):
            return node.value

        if isinstance(node, Var):
            return env.get(node.name, node.line, node.col)

        if isinstance(node, ListLit):
            return [self.eval(item, env) for item in node.items]

        if isinstance(node, MapLit):
            result = {}
            for key_node, value_node in node.pairs:
                key = self.eval(key_node, env)
                if not isinstance(key, str):
                    key = to_string(key)
                result[key] = self.eval(value_node, env)
            return result

        if isinstance(node, FuncLit):
            return SnegFunction(node.params, node.body, env, node.name)

        if isinstance(node, Unary):
            value = self.eval(node.operand, env)
            if node.op == "not":
                return not is_truthy(value)
            if not is_number(value):
                raise SnegError(f"нельзя взять минус от {type_name(value)}",
                                node.line, node.col)
            return -value

        if isinstance(node, Logical):
            left = self.eval(node.left, env)
            if node.op == "and":
                return self.eval(node.right, env) if is_truthy(left) else left
            return left if is_truthy(left) else self.eval(node.right, env)

        if isinstance(node, Binary):
            left = self.eval(node.left, env)
            right = self.eval(node.right, env)
            return self.binary_op(node.op, left, right, node)

        if isinstance(node, Get):
            obj = self.eval(node.obj, env)
            return self.get_member(obj, node.name, node)

        if isinstance(node, Index):
            obj = self.eval(node.obj, env)
            index = self.eval(node.index, env)
            return self.get_index(obj, index, node)

        if isinstance(node, Call):
            callee = self.eval(node.callee, env)
            args = [self.eval(arg, env) for arg in node.args]
            return self.call_value(callee, args, node)

        raise SnegError(f"неизвестное выражение {type(node).__name__}",
                        node.line, node.col)

    # --- арифметика и сравнения -------------------------------------------
    def binary_op(self, op, left, right, node):
        line, col = node.line, node.col

        if op == "+":
            if isinstance(left, str) or isinstance(right, str):
                return to_string(left) + to_string(right)
            a, b = self.need_numbers(op, left, right, node)
            return a + b

        if op in ("-", "*", "/", "%"):
            a, b = self.need_numbers(op, left, right, node)
            if op == "/":
                if b == 0:
                    raise SnegError("деление на ноль", line, col)
                return a / b
            if op == "%":
                if b == 0:
                    raise SnegError("остаток от деления на ноль", line, col)
                return math.fmod(a, b)
            return a - b if op == "-" else a * b

        if op == "==":
            return values_equal(left, right)

        if op == "!=":
            return not values_equal(left, right)

        if op in ("<", "<=", ">", ">="):
            if is_number(left) and is_number(right):
                pass
            elif isinstance(left, str) and isinstance(right, str):
                pass
            else:
                raise SnegError(
                    f"нельзя сравнить {type_name(left)} и {type_name(right)}",
                    line, col)

            if op == "<":
                return left < right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            return left >= right

        if op == "in":
            return self.contains(right, left, node)

        raise SnegError(f"неизвестная операция {op!r}", line, col)

    def need_numbers(self, op, left, right, node):
        if is_number(left) and is_number(right):
            return left, right
        raise SnegError(
            f"операция {op!r} не работает с {type_name(left)} и {type_name(right)}",
            node.line, node.col)

    def contains(self, container, value, node):
        if isinstance(container, list):
            return any(values_equal(item, value) for item in container)
        if isinstance(container, dict):
            key = value if isinstance(value, str) else to_string(value)
            return key in container
        if isinstance(container, str):
            return to_string(value) in container
        if isinstance(container, SnegRange):
            return container.contains(value)
        raise SnegError(
            f"проверка in не работает с {type_name(container)}", node.line, node.col)

    # --- доступ к элементам -----------------------------------------------
    def get_member(self, obj, name, node):
        if isinstance(obj, dict):
            return obj.get(name)
        if obj is None:
            raise SnegError(f"у nil нет поля {name!r}", node.line, node.col)
        if isinstance(obj, list) and name == "length":
            return float(len(obj))
        raise SnegError(f"у {type_name(obj)} нет поля {name!r}", node.line, node.col)

    def get_index(self, obj, index, node):
        if isinstance(obj, list):
            i = self.need_index(index, node)
            if i < 0 or i >= len(obj):
                raise SnegError(f"индекс {i} вне списка (длина {len(obj)})",
                                node.line, node.col)
            return obj[i]

        if isinstance(obj, dict):
            key = index if isinstance(index, str) else to_string(index)
            return obj.get(key)

        if isinstance(obj, str):
            i = self.need_index(index, node)
            if i < 0 or i >= len(obj):
                raise SnegError(f"индекс {i} вне строки (длина {len(obj)})",
                                node.line, node.col)
            return obj[i]

        raise SnegError(f"{type_name(obj)} нельзя индексировать", node.line, node.col)

    def set_index(self, obj, index, value, node):
        if isinstance(obj, list):
            i = self.need_index(index, node)
            if i < 0 or i >= len(obj):
                raise SnegError(
                    f"индекс {i} вне списка (длина {len(obj)}) — новый элемент добавляй через push",
                    node.line, node.col)
            obj[i] = value
            return

        if isinstance(obj, dict):
            key = index if isinstance(index, str) else to_string(index)
            obj[key] = value
            return

        raise SnegError(f"{type_name(obj)} нельзя индексировать", node.line, node.col)

    def need_index(self, index, node):
        if not is_number(index):
            raise SnegError(f"индекс должен быть числом, а не {type_name(index)}",
                            node.line, node.col)
        if not index.is_integer():
            raise SnegError(f"индекс должен быть целым, а получено {fmt_number(index)}",
                            node.line, node.col)
        return int(index)

    def iterate(self, value, node):
        if isinstance(value, (list, dict, str, SnegRange)):
            return list(value)
        raise SnegError(
            f"по {type_name(value)} нельзя пройти циклом for — "
            f"нужен список, таблица, строка или range", node.line, node.col)

    # --- вызов функций ----------------------------------------------------
    def call_value(self, func, args, node):
        if isinstance(func, Builtin):
            return func.func(args, node.line, node.col)

        if isinstance(func, SnegFunction):
            if len(args) != len(func.params):
                raise SnegError(
                    f"функция {func.name} ждёт {len(func.params)} аргумент(ов), "
                    f"а получила {len(args)}", node.line, node.col)

            scope = Environment(func.closure, func.name)
            for name, value in zip(func.params, args):
                scope.define(name, value)

            try:
                self.exec_block(func.body, scope)
            except ReturnSignal as ret:
                return ret.value
            except SnegError as err:
                err.frames.insert(
                    0, f"в функции {func.name}(), вызвана на строке {node.line}")
                raise
            return None

        raise SnegError(
            f"нельзя вызвать {type_name(func)} как функцию — это не функция",
            node.line, node.col)

    # --- встроенные функции -----------------------------------------------
    def install_builtins(self):
        g = self.globals

        def add(name, func):
            g.define(name, Builtin(name, func))

        def need(args, count, name):
            if len(args) != count:
                raise SnegError(
                    f"{name} ждёт {count} аргумент(ов), а получила {len(args)}")

        # --- ввод и вывод ---
        def bi_print(args, line, col):
            self.write(" ".join(to_string(v) for v in args) + "\n")
            return None

        def bi_input(args, line, col):
            if args:
                self.write(to_string(args[0]))
            data = self.stdin.readline()
            if data == "":
                return None
            return data.rstrip("\n").rstrip("\r")

        add("print", bi_print)
        add("input", bi_input)

        # --- типы и строки ---
        def bi_type(args, line, col):
            need(args, 1, "type")
            return type_name(args[0])

        def bi_str(args, line, col):
            need(args, 1, "str")
            return to_string(args[0])

        def bi_num(args, line, col):
            need(args, 1, "num")
            value = args[0]
            if is_number(value):
                return value
            if isinstance(value, str):
                try:
                    return float(value.strip().replace(",", "."))
                except ValueError:
                    return None
            return None

        def bi_len(args, line, col):
            need(args, 1, "len")
            value = args[0]
            if isinstance(value, (str, list, dict, SnegRange)):
                return float(len(value))
            raise SnegError(f"len не работает с {type_name(value)}", line, col)

        add("type", bi_type)
        add("str", bi_str)
        add("num", bi_num)
        add("len", bi_len)

        def bi_upper(args, line, col):
            need(args, 1, "upper")
            return to_string(args[0]).upper()

        def bi_lower(args, line, col):
            need(args, 1, "lower")
            return to_string(args[0]).lower()

        def bi_split(args, line, col):
            need(args, 2, "split")
            if not isinstance(args[0], str):
                raise SnegError("split ждёт строку первым аргументом", line, col)
            return args[0].split(to_string(args[1]))

        def bi_join(args, line, col):
            need(args, 2, "join")
            if not isinstance(args[0], list):
                raise SnegError("join ждёт список первым аргументом", line, col)
            return to_string(args[1]).join(to_string(v) for v in args[0])

        add("upper", bi_upper)
        add("lower", bi_lower)
        add("split", bi_split)
        add("join", bi_join)

        # --- списки ---
        def need_list(args, name, line, col):
            if not isinstance(args[0], list):
                raise SnegError(
                    f"{name} ждёт список первым аргументом, "
                    f"а получила {type_name(args[0])}", line, col)
            return args[0]

        def bi_push(args, line, col):
            need(args, 2, "push")
            need_list(args, "push", line, col).append(args[1])
            return args[0]

        def bi_pop(args, line, col):
            need(args, 1, "pop")
            target = need_list(args, "pop", line, col)
            return target.pop() if target else None

        def bi_insert(args, line, col):
            need(args, 3, "insert")
            target = need_list(args, "insert", line, col)
            idx = int(args[1]) if is_number(args[1]) else len(target)
            target.insert(max(0, min(idx, len(target))), args[2])
            return target

        def bi_remove(args, line, col):
            need(args, 2, "remove")
            target = need_list(args, "remove", line, col)
            for i, item in enumerate(target):
                if values_equal(item, args[1]):
                    target.pop(i)
                    return True
            return False

        def bi_sort(args, line, col):
            need(args, 1, "sort")
            target = need_list(args, "sort", line, col)
            try:
                target.sort()
            except TypeError:
                raise SnegError("sort умеет сортировать только одинаковые элементы",
                                line, col)
            return target

        add("push", bi_push)
        add("pop", bi_pop)
        add("insert", bi_insert)
        add("remove", bi_remove)
        add("sort", bi_sort)

        # --- таблицы ---
        def bi_keys(args, line, col):
            need(args, 1, "keys")
            if isinstance(args[0], dict):
                return list(args[0].keys())
            raise SnegError("keys ждёт таблицу", line, col)

        def bi_values(args, line, col):
            need(args, 1, "values")
            if isinstance(args[0], dict):
                return list(args[0].values())
            raise SnegError("values ждёт таблицу", line, col)

        def bi_delete(args, line, col):
            need(args, 2, "delete")
            if not isinstance(args[0], dict):
                raise SnegError("delete ждёт таблицу", line, col)
            key = args[1] if isinstance(args[1], str) else to_string(args[1])
            return args[0].pop(key, None) is not None

        def bi_has(args, line, col):
            need(args, 2, "has")
            return self.contains(args[0], args[1], Node(line, col))

        add("keys", bi_keys)
        add("values", bi_values)
        add("delete", bi_delete)
        add("has", bi_has)

        # --- числа и диапазоны ---
        def bi_range(args, line, col):
            if not 1 <= len(args) <= 3:
                raise SnegError(
                    f"range ждёт от 1 до 3 аргументов, а получила {len(args)}")
            for value in args:
                if not is_number(value):
                    raise SnegError("range ждёт числа")
            if len(args) == 1:
                return SnegRange(0.0, args[0], 1.0)
            if len(args) == 2:
                return SnegRange(args[0], args[1], 1.0)
            if args[2] == 0:
                raise SnegError("шаг range не может быть нулём")
            return SnegRange(args[0], args[1], args[2])

        add("range", bi_range)

        def num_func(name, py_func):
            def wrapped(args, line, col):
                need(args, 1, name)
                if not is_number(args[0]):
                    raise SnegError(
                        f"{name} ждёт число, а получила {type_name(args[0])}", line, col)
                try:
                    return py_func(args[0])
                except ValueError:
                    return None
            return wrapped

        add("abs", num_func("abs", abs))
        add("floor", num_func("floor", lambda x: float(math.floor(x))))
        add("ceil", num_func("ceil", lambda x: float(math.ceil(x))))
        # округление половины вверх (2.5 -> 3), как в C# и Lua,
        # а не банковское, как в Python по умолчанию
        def _round_half_up(x):
            if x >= 0:
                return float(math.floor(x + 0.5))
            return float(math.ceil(x - 0.5))

        add("round", num_func("round", _round_half_up))

        def bi_sqrt(args, line, col):
            need(args, 1, "sqrt")
            if not is_number(args[0]):
                raise SnegError("sqrt ждёт число", line, col)
            if args[0] < 0:
                raise SnegError("корень из отрицательного числа", line, col)
            return math.sqrt(args[0])

        add("sqrt", bi_sqrt)

        def bi_min(args, line, col):
            if not args:
                raise SnegError("min ждёт хотя бы один аргумент")
            if len(args) == 1 and isinstance(args[0], list):
                return min(args[0]) if args[0] else None
            return min(args)

        def bi_max(args, line, col):
            if not args:
                raise SnegError("max ждёт хотя бы один аргумент")
            if len(args) == 1 and isinstance(args[0], list):
                return max(args[0]) if args[0] else None
            return max(args)

        add("min", bi_min)
        add("max", bi_max)

        # --- случайность ---
        def bi_random(args, line, col):
            return random.random()

        def bi_randint(args, line, col):
            need(args, 2, "randint")
            if not (is_number(args[0]) and is_number(args[1])):
                raise SnegError("randint ждёт два числа", line, col)
            return float(random.randint(int(args[0]), int(args[1])))

        def bi_chance(args, line, col):
            need(args, 1, "chance")
            if not is_number(args[0]):
                raise SnegError("chance ждёт число от 0 до 100", line, col)
            return random.random() * 100 < args[0]

        add("random", bi_random)
        add("randint", bi_randint)
        add("chance", bi_chance)

        # --- служебное ---
        def bi_clock(args, line, col):
            return time.time()

        def bi_error(args, line, col):
            raise SnegError(to_string(args[0]) if args else "ошибка", line, col)

        def bi_assert(args, line, col):
            if len(args) not in (1, 2):
                raise SnegError("assert ждёт условие и, по желанию, сообщение")
            if not is_truthy(args[0]):
                message = to_string(args[1]) if len(args) > 1 else "проверка не прошла"
                raise SnegError(message, line, col)
            return None

        def bi_exit(args, line, col):
            code = int(args[0]) if args and is_number(args[0]) else 0
            raise SystemExit(code)

        add("clock", bi_clock)
        add("error", bi_error)
        add("assert", bi_assert)
        add("exit", bi_exit)


# ===========================================================================
#  8. ЗАПУСК
# ===========================================================================

def parse_source(source, filename="<программа>"):
    """Текст -> токены -> дерево. Возвращает (дерево, токены)."""
    tokens = Lexer(source, filename).tokenize()
    return Parser(tokens).parse(), tokens


# --- компиляция в .snegc (аналог .pyc) -------------------------------

def _node_classes():
    """Собирает все классы узлов дерева по имени — нужно для загрузки .snegc."""
    found = {}

    def walk(cls):
        for sub in cls.__subclasses__():
            if sub.__name__ not in found:
                found[sub.__name__] = sub
                walk(sub)

    walk(Node)
    return found


NODE_TYPES = _node_classes()
COMPILED_EXT = ".snegc"


def node_to_data(obj):
    """Дерево -> обычный JSON-совместимый словарь."""
    if isinstance(obj, Node):
        return {
            "t": type(obj).__name__,
            "f": {key: node_to_data(value) for key, value in obj.__dict__.items()},
        }
    if isinstance(obj, (list, tuple)):
        # пары в таблицах — кортежи, в JSON они станут списками; при загрузке
        # это не мешает: кортежи там распаковываются так же
        return [node_to_data(item) for item in obj]
    if isinstance(obj, dict):
        return {key: node_to_data(value) for key, value in obj.items()}
    return obj


def data_to_node(data):
    """JSON-словарь -> снова дерево."""
    if isinstance(data, list):
        return [data_to_node(item) for item in data]

    if isinstance(data, dict):
        if "t" in data and "f" in data:
            cls = NODE_TYPES.get(data["t"])
            if cls is None:
                raise SnegError(
                    f"файл собран другой версией sneg (неизвестный узел {data['t']!r})")
            node = cls.__new__(cls)
            for key, value in data["f"].items():
                setattr(node, key, data_to_node(value))
            return node
        return {key: data_to_node(value) for key, value in data.items()}

    return data


def compile_file(path, out_path=None):
    """Разбирает .sneg и сохраняет дерево в .snegc — как py_compile делает .pyc.
    Возвращает путь к созданному файлу."""
    source = open(path, encoding="utf-8").read()
    program, _ = parse_source(source, os.path.basename(path))

    payload = {
        "sneg": VERSION,
        "source": os.path.basename(path),
        "nodes": node_to_data(program),
    }

    target = out_path or (path + "c")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)

    return target


def load_compiled(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or "nodes" not in payload:
        raise SnegError("это не скомпилированный файл sneg")
    return data_to_node(payload["nodes"])


def run_source(source, filename="<программа>", out=None, stdin=None, interp=None):
    """Выполняет текст программы и возвращает интерпретатор."""
    program, _ = parse_source(source, filename)
    machine = interp or Interpreter(out, stdin)

    try:
        machine.execute(program)
    except ReturnSignal:
        raise SnegError("return можно использовать только внутри функции")
    except BreakSignal:
        raise SnegError("break можно использовать только внутри цикла")
    except ContinueSignal:
        raise SnegError("continue можно использовать только внутри цикла")

    return machine


def run_file(path, out=None, stdin=None, interactive=False):
    name = os.path.basename(path)
    machine = Interpreter(out, stdin)
    source = ""

    try:
        if path.endswith(COMPILED_EXT):
            program = load_compiled(path)
        else:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            program, _ = parse_source(source, name)

        try:
            machine.execute(program)
        except ReturnSignal:
            raise SnegError("return можно использовать только внутри функции")
        except BreakSignal:
            raise SnegError("break можно использовать только внутри цикла")
        except ContinueSignal:
            raise SnegError("continue можно использовать только внутри цикла")

        if interactive:
            print(f"\n--- {name}: выполнено, продолжаем в REPL с его переменными ---")
            return repl(machine)

        return 0

    except SnegError as err:
        text = format_error(err, source.splitlines() if source else [], name)
        print(text, file=sys.stderr)
        return 1


def dump(node, indent=0):
    """Печатник дерева — посмотреть, как парсер понял программу."""
    pad = "  " * indent

    if isinstance(node, list):
        for item in node:
            dump(item, indent)
        return

    if not isinstance(node, Node):
        print(f"{pad}{node!r}")
        return

    scalars = []
    children = []
    for key, value in node.__dict__.items():
        if key in ("line", "col"):
            continue
        if isinstance(value, (Node, list)):
            children.append((key, value))
        else:
            scalars.append(f"{key}={value!r}")

    head = f"{pad}{type(node).__name__}"
    if scalars:
        head += "(" + ", ".join(scalars) + ")"
    print(head)

    for key, value in children:
        print(f"{pad}  .{key}:")
        dump(value, indent + 2)


def repl(machine=None):
    machine = machine or Interpreter()
    print(f"sneg {VERSION} — интерактивный режим. Выход: Ctrl+D или exit()\n")

    buffer = ""
    while True:
        try:
            prompt = "sneg> " if not buffer else "   ... "
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not buffer and line.strip() in ("exit()", "quit", "выход"):
            return 0

        buffer += line + "\n"

        # пока скобки не сомкнулись — продолжаем ввод
        if buffer.count("{") > buffer.count("}") or buffer.count("(") > buffer.count(")"):
            continue

        if not buffer.strip():
            buffer = ""
            continue

        try:
            program, _ = parse_source(buffer, "<repl>")
            for stmt in program:
                # в REPL полезно видеть результат выражения, как в Python,
                # поэтому выражение вычисляем сами — ровно один раз
                if isinstance(stmt, ExprStmt):
                    value = machine.eval(stmt.expr, machine.globals)
                    if value is not None:
                        print(to_string(value))
                else:
                    machine.execute([stmt])
        except SnegError as err:
            print(format_error(err, buffer.splitlines(), "<repl>"))
        except SystemExit:
            return 0
        except Exception as err:        # внутренняя ошибка не должна убивать REPL
            print(f"\n\u274c {type(err).__name__}: {err}")

        buffer = ""


def _windows_parent_name():
    """Имя родительского процесса. При двойном клике это explorer.exe."""
    if not sys.platform.startswith("win"):
        return ""

    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x02

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return ""

        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)

        table = {}
        if kernel32.Process32First(snapshot, ctypes.byref(entry)):
            while True:
                table[entry.th32ProcessID] = (
                    entry.th32ParentProcessID,
                    entry.szExeFile.decode("mbcs", "replace"),
                )
                if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                    break

        kernel32.CloseHandle(snapshot)

        # PyInstaller запускает два процесса собственного exe (загрузчик и сам скрипт),
        # поэтому идём вверх по цепочке, пока не найдём «настоящего» родителя
        own = {os.path.basename(sys.executable).lower(), "sneg.exe"}
        pid = os.getpid()

        for _ in range(8):
            info = table.get(pid)
            if not info:
                return ""
            parent = table.get(info[0])
            if not parent:
                return ""
            if parent[1].lower() not in own:
                return parent[1]
            pid = info[0]

        return ""
    except Exception:
        return ""


# родители, после которых консольное окно сразу исчезает
VANISHING_PARENTS = {"explorer.exe", "explorer"}


def should_pause_on_exit():
    """Стоит ли ждать Enter перед закрытием окна."""
    if os.environ.get("SNEG_NO_PAUSE"):
        return False
    if not sys.stdin or not sys.stdin.isatty():
        return False        # ввод перенаправлен — ждать некого
    return _windows_parent_name().lower() in VANISHING_PARENTS


def log_path():
    """Файл журнала рядом с программой (exe или sneg.py)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "sneg-log.txt")


def log_event(message):
    """Дописывает строку в журнал. Не получилось — молчим, это не повод падать."""
    path = log_path()
    try:
        # BOM только при создании файла — иначе Блокнот и PowerShell
        # показывают русский текст абракадаброй
        fresh = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", encoding="utf-8-sig" if fresh else "utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def main(argv=None):
    """Обёртка над _main: после работы ждёт Enter, если окно вот-вот исчезнет."""
    args = list(sys.argv[1:] if argv is None else argv)

    forced = None
    if "--pause" in args:
        forced = True
        args = [a for a in args if a != "--pause"]
    if "--no-pause" in args:
        forced = False
        args = [a for a in args if a != "--no-pause"]

    if os.environ.get("SNEG_DEBUG"):
        try:
            tty = bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            tty = False
        log_event(
            f"старт {VERSION}: argv={args}, cwd={os.getcwd()}, "
            f"frozen={getattr(sys, 'frozen', False)}, "
            f"launcher={_windows_parent_name() or '?'}, tty={tty}")

    try:
        code = _main(args)
    except BaseException:
        import traceback
        log_event("КРАШ:\n" + traceback.format_exc())
        raise

    pause = forced if forced is not None else should_pause_on_exit()
    if pause:
        try:
            input("\nНажми Enter, чтобы закрыть окно... ")
        except (EOFError, KeyboardInterrupt):
            pass

    return code


def _main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        return repl()

    if args[0] in ("-h", "--help", "-?"):
        print(__doc__)
        return 0

    if args[0] in ("-v", "--version"):
        print(f"sneg {VERSION}")
        return 0

    if args[0] == "--who-am-i":
        launcher = _windows_parent_name() or "неизвестно"
        print(f"запущен из: {launcher}")
        print(f"ждать Enter при выходе: {'да' if should_pause_on_exit() else 'нет'}")
        return 0

    interactive = False
    if args[0] in ("-i", "--interactive"):
        interactive = True
        args = args[1:]
        if not args:
            return repl()

    mode = "run"
    if args[0] in ("--tokens", "--ast", "--check", "--compile"):
        mode = args[0][2:]
        args = args[1:]

    if not args:
        print("не указан файл с программой", file=sys.stderr)
        return 2

    path = args[0]
    if not os.path.exists(path):
        print(f"файл не найден: {path}", file=sys.stderr)
        return 2

    source = open(path, encoding="utf-8").read()
    name = os.path.basename(path)

    if mode == "tokens":
        for token in Lexer(source, name).tokenize():
            print(token)
        return 0

    if mode == "ast":
        program, _ = parse_source(source, name)
        dump(program)
        return 0

    if mode == "check":
        try:
            program, _ = parse_source(source, name)
        except SnegError as err:
            print(format_error(err, source.splitlines(), name), file=sys.stderr)
            return 1
        print(f"{name}: синтаксис в порядке, операторов верхнего уровня: {len(program)}")
        return 0

    if mode == "compile":
        try:
            target = compile_file(path, args[1] if len(args) > 1 else None)
        except SnegError as err:
            print(format_error(err, source.splitlines(), name), file=sys.stderr)
            return 1
        print(f"собрано: {target}")
        return 0

    return run_file(path, interactive=interactive)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        import traceback
        crash = traceback.format_exc()
        log_event("КРАШ ДО СТАРТА:\n" + crash)
        print(crash, file=sys.stderr)
        raise SystemExit(1)
