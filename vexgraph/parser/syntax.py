"""The grammar: VEX tokens into a tree.

Expressions use precedence climbing, statements recursive descent — the ordinary
shapes, because VEX's expression grammar is C's and there is nothing to be
gained by being clever about it.

Two things are deliberately unusual. Every statement records the source span it
came from, so anything the graph cannot express can be handed back verbatim. And
`{` is ambiguous in VEX — a block in statement position, a vector literal in
expression position — so the parser tracks which it is expecting rather than
guessing from the contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .lexer import Kind, Token, tokenize


# --------------------------------------------------------------- expressions

@dataclass
class Expr:
    pass


@dataclass
class Literal(Expr):
    text: str
    kind: str            # "int" | "float" | "string"


@dataclass
class VectorLiteral(Expr):
    items: list[Expr]


@dataclass
class Attribute(Expr):
    name: str
    prefix: str = ""
    is_array: bool = False


@dataclass
class Name(Expr):
    name: str


@dataclass
class Unary(Expr):
    op: str
    operand: Expr


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Ternary(Expr):
    condition: Expr
    then: Expr
    otherwise: Expr


@dataclass
class Call(Expr):
    name: str
    args: list[Expr]


@dataclass
class Index(Expr):
    target: Expr
    index: Expr


@dataclass
class Member(Expr):
    target: Expr
    name: str


@dataclass
class Cast(Expr):
    to: str
    value: Expr


@dataclass
class Hscript(Expr):
    """`$PI`, `$F` - expanded by Houdini before VEX sees it, so pass it through."""
    text: str


# ---------------------------------------------------------------- statements

@dataclass
class Statement:
    # Byte offsets into the original source. The escape hatch depends on these.
    start: int = 0
    end: int = 0


@dataclass
class Declare(Statement):
    type: str = ""
    name: str = ""
    value: Expr | None = None
    is_array: bool = False


@dataclass
class Assign(Statement):
    target: Expr = None          # Attribute | Name | Index | Member
    op: str = "="                # "=", "+=", ...
    value: Expr = None


@dataclass
class ExprStatement(Statement):
    value: Expr = None


@dataclass
class Block(Statement):
    body: list[Statement] = field(default_factory=list)


@dataclass
class If(Statement):
    condition: Expr = None
    then: list[Statement] = field(default_factory=list)
    otherwise: list[Statement] = field(default_factory=list)


@dataclass
class For(Statement):
    setup: Statement | None = None
    condition: Expr | None = None
    step: Statement | None = None
    body: list[Statement] = field(default_factory=list)


@dataclass
class ForEach(Statement):
    index_name: str = ""
    value_name: str = ""
    value_type: str = ""
    array: Expr = None
    body: list[Statement] = field(default_factory=list)


@dataclass
class While(Statement):
    condition: Expr = None
    body: list[Statement] = field(default_factory=list)


@dataclass
class Jump(Statement):
    word: str = "break"          # "break" | "continue"


@dataclass
class Raw(Statement):
    """Something the parser could read but the grammar here does not model."""
    text: str = ""
    # Set when this came from a parse *failure* rather than a known-unmodellable
    # construct, so the report can tell the user which line defeated it.
    reason: str = ""


class ParseError(SyntaxError):
    def __init__(self, message: str, token: Token):
        super().__init__(f"line {token.line}: {message}")
        self.token = token


# Binding power per binary operator, loosest first — the same table VEX
# inherits from C.
PRECEDENCE = {
    "||": 1, "&&": 2,
    "|": 3, "^": 4, "&": 5,
    "==": 6, "!=": 6,
    "<": 7, ">": 7, "<=": 7, ">=": 7,
    "+": 9, "-": 9,
    "*": 10, "/": 10, "%": 10,
}

ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%="}


class Parser:
    def __init__(self, source: str):
        self.source = source
        self.tokens = tokenize(source)
        self.position = 0
        # `vector a, b, c;` is one statement in the text and three in the graph.
        # The extra two wait here and are handed out by the statement loop, so
        # every caller keeps seeing one statement per call.
        self._pending: list[Statement] = []

    # ------------------------------------------------------------- plumbing

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def peek(self, ahead: int = 1) -> Token:
        return self.tokens[min(self.position + ahead, len(self.tokens) - 1)]

    def advance(self) -> Token:
        token = self.tokens[self.position]
        if token.kind is not Kind.END:
            self.position += 1
        return token

    def accept(self, kind: Kind, text: str | None = None) -> Token | None:
        if self.current.is_(kind, text):
            return self.advance()
        return None

    def expect(self, kind: Kind, text: str | None = None) -> Token:
        token = self.accept(kind, text)
        if token is None:
            wanted = text or kind.value
            raise ParseError(f"expected {wanted!r}, found {self.current.text!r}",
                             self.current)
        return token

    # ------------------------------------------------------------ statements

    def parse(self) -> list[Statement]:
        body: list[Statement] = []
        while self.current.kind is not Kind.END:
            body.append(self.recovering_statement())
            body.extend(self._drain())
        return body

    def _drain(self) -> list[Statement]:
        """The extra declarations the last statement produced, if any."""
        pending, self._pending = self._pending, []
        return pending

    def recovering_statement(self) -> Statement:
        """One unreadable statement must not cost the reader all the others.

        Failing the whole import over a single line is how a snippet that is
        95% ordinary VEX ends up as one opaque Inline node. On a parse error we
        keep that statement verbatim, resynchronise at the next `;`, and carry
        on, so the rest of the snippet still arrives as nodes.
        """
        start = self.current.start
        mark = self.position
        try:
            return self.statement()
        except ParseError as exc:
            self.position = mark
            self._pending.clear()      # half a declaration list is not useful
            raw = self.raw_from(start)
            raw.reason = str(exc)
            if self.position == mark:      # recovery made no progress
                self.advance()
            return raw

    def statement(self) -> Statement:
        start = self.current.start
        token = self.current

        if token.is_(Kind.PUNCT, ";"):
            self.advance()
            return Block(start, self.tokens[self.position - 1].end)
        if token.is_(Kind.PUNCT, "{"):
            return self.block()
        if token.is_(Kind.KEYWORD, "if"):
            return self.if_statement()
        if token.is_(Kind.KEYWORD, "for"):
            return self.for_statement()
        if token.is_(Kind.KEYWORD, "foreach"):
            return self.foreach_statement()
        if token.is_(Kind.KEYWORD, "while"):
            return self.while_statement()
        if token.kind is Kind.KEYWORD and token.text in ("break", "continue"):
            self.advance()
            self.accept(Kind.PUNCT, ";")
            return Jump(start, self.tokens[self.position - 1].end, word=token.text)
        if token.kind is Kind.KEYWORD:
            # `do`, `return`, `function`, `struct` — readable, not modellable.
            return self.raw_statement()
        if token.kind is Kind.TYPE:
            return self.declaration()

        return self.expression_statement()

    def block(self) -> Block:
        start = self.expect(Kind.PUNCT, "{").start
        body: list[Statement] = []
        while not self.current.is_(Kind.PUNCT, "}"):
            if self.current.kind is Kind.END:
                raise ParseError("unterminated block", self.current)
            body.append(self.recovering_statement())
        end = self.expect(Kind.PUNCT, "}").end
        return Block(start, end, body=body)

    def body(self) -> list[Statement]:
        """A branch or loop body, braced or a single statement."""
        if self.current.is_(Kind.PUNCT, "{"):
            return self.block().body
        return [self.statement(), *self._drain()]

    def declaration(self) -> Statement:
        start = self.current.start
        type_name = self.expect(Kind.TYPE).text

        # `vector[] name` and `vector name[]` both occur; VEX only accepts the
        # second when declaring, but the first shows up in casts and returns.
        is_array = False
        if self.current.is_(Kind.PUNCT, "["):
            self.advance()
            self.expect(Kind.PUNCT, "]")
            is_array = True

        if self.current.kind is not Kind.NAME:
            return self.raw_from(start)

        # `vector t, tc, bt;` declares three variables. This used to be handed
        # back as one Raw statement, which cost far more than the line itself:
        # the graph then had no record of those names, so every later
        # assignment to them was refused too and the whole snippet collapsed
        # into inline VEX. One Declare per name is what the graph already
        # models, so the split happens here.
        declared: list[tuple[str, Expr | None, bool]] = []
        while True:
            name = self.advance().text
            name_is_array = is_array
            if self.current.is_(Kind.PUNCT, "["):
                self.advance()
                self.expect(Kind.PUNCT, "]")
                name_is_array = True

            value = None
            if self.accept(Kind.OP, "="):
                value = self.expression()
            declared.append((name, value, name_is_array))

            if not self.accept(Kind.PUNCT, ","):
                break
            if self.current.kind is not Kind.NAME:
                return self.raw_from(start)

        end = self.expect(Kind.PUNCT, ";").end
        statements = [Declare(start, end, type=type_name, name=name,
                              value=value, is_array=array)
                      for name, value, array in declared]
        self._pending.extend(statements[1:])
        return statements[0]

    def if_statement(self) -> Statement:
        start = self.expect(Kind.KEYWORD, "if").start
        self.expect(Kind.PUNCT, "(")
        condition = self.expression()
        self.expect(Kind.PUNCT, ")")
        then = self.body()
        otherwise: list[Statement] = []
        if self.accept(Kind.KEYWORD, "else"):
            otherwise = self.body()
        end = self.tokens[self.position - 1].end
        return If(start, end, condition=condition, then=then, otherwise=otherwise)

    def for_statement(self) -> Statement:
        start = self.expect(Kind.KEYWORD, "for").start
        self.expect(Kind.PUNCT, "(")

        # `for (value : array)` is a different construct; not modelled.
        setup = None
        if not self.current.is_(Kind.PUNCT, ";"):
            setup = (self.declaration() if self.current.kind is Kind.TYPE
                     else self.expression_statement())
        else:
            self.advance()

        condition = None if self.current.is_(Kind.PUNCT, ";") else self.expression()
        self.expect(Kind.PUNCT, ";")
        step = None
        if not self.current.is_(Kind.PUNCT, ")"):
            step = self.bare_expression_statement()
        self.expect(Kind.PUNCT, ")")
        body = self.body()
        end = self.tokens[self.position - 1].end
        return For(start, end, setup=setup, condition=condition, step=step,
                   body=body)

    def foreach_statement(self) -> Statement:
        start = self.expect(Kind.KEYWORD, "foreach").start
        self.expect(Kind.PUNCT, "(")

        first_type = self.accept(Kind.TYPE)
        first = self.expect(Kind.NAME).text
        index_name, value_name, value_type = "", first, first_type.text if first_type else ""

        if self.accept(Kind.PUNCT, ","):
            second_type = self.accept(Kind.TYPE)
            index_name, value_name = first, self.expect(Kind.NAME).text
            value_type = second_type.text if second_type else ""

        self.expect(Kind.PUNCT, ";")
        array = self.expression()
        self.expect(Kind.PUNCT, ")")
        body = self.body()
        end = self.tokens[self.position - 1].end
        return ForEach(start, end, index_name=index_name, value_name=value_name,
                       value_type=value_type, array=array, body=body)

    def while_statement(self) -> Statement:
        start = self.expect(Kind.KEYWORD, "while").start
        self.expect(Kind.PUNCT, "(")
        condition = self.expression()
        self.expect(Kind.PUNCT, ")")
        body = self.body()
        end = self.tokens[self.position - 1].end
        return While(start, end, condition=condition, body=body)

    def expression_statement(self) -> Statement:
        statement = self.bare_expression_statement()
        statement.end = self.expect(Kind.PUNCT, ";").end
        return statement

    def bare_expression_statement(self) -> Statement:
        """An expression statement without its semicolon — also a `for` step."""
        start = self.current.start

        # `++x` is `x += 1` just as `x++` is. Refusing the prefix spelling
        # did not fail cleanly: the surrounding `for` header stopped parsing
        # as a loop and its pieces became separate statements, which emitted
        # as separate lines - a syntax error after the round trip.
        if self.current.kind is Kind.OP and self.current.text in ("++", "--"):
            op = self.advance().text
            target = self.expression()
            return Assign(start, self.tokens[self.position - 1].end,
                          target=target, op="+=" if op == "++" else "-=",
                          value=Literal("1", "int"))

        target = self.expression()

        if self.current.kind is Kind.OP and self.current.text in ASSIGN_OPS:
            op = self.advance().text
            value = self.expression()
            return Assign(start, self.tokens[self.position - 1].end,
                          target=target, op=op, value=value)

        # `i++` in a for-step is an assignment in disguise; writing it as one
        # lets the loop matcher recognise the canonical shape.
        if self.current.kind is Kind.OP and self.current.text in ("++", "--"):
            op = self.advance().text
            return Assign(start, self.tokens[self.position - 1].end,
                          target=target, op="+=" if op == "++" else "-=",
                          value=Literal("1", "int"))

        return ExprStatement(start, self.tokens[self.position - 1].end,
                             value=target)

    def raw_statement(self) -> Statement:
        return self.raw_from(self.current.start)

    def raw_from(self, start: int) -> Raw:
        """Consume to the end of the statement and keep the text as written."""
        depth = 0
        while self.current.kind is not Kind.END:
            token = self.current
            if token.is_(Kind.PUNCT, "{"):
                depth += 1
            elif token.is_(Kind.PUNCT, "}"):
                depth -= 1
                self.advance()
                if depth <= 0:
                    # `do { ... } while (cond);` is one statement whose tail
                    # comes *after* the closing brace. Stopping at the brace
                    # split it in two: a `do` block with no while - which does
                    # not compile - and a stray while-loop statement.
                    if self.current.is_(Kind.KEYWORD, "while"):
                        while (self.current.kind is not Kind.END
                               and not self.current.is_(Kind.PUNCT, ";")):
                            self.advance()
                    # `struct x { ... };` ends with a semicolon that belongs to
                    # the declaration, not to an empty statement after it.
                    self.accept(Kind.PUNCT, ";")
                    break
                continue
            elif token.is_(Kind.PUNCT, ";") and depth == 0:
                self.advance()
                break
            self.advance()
        end = self.tokens[self.position - 1].end
        return Raw(start, end, text=self.source[start:end])

    # ----------------------------------------------------------- expressions

    def expression(self, minimum: int = 0) -> Expr:
        left = self.unary()

        while True:
            token = self.current
            if token.kind is not Kind.OP:
                break
            if token.text == "?" and minimum <= 0:
                self.advance()
                then = self.expression()
                self.expect(Kind.OP, ":")
                left = Ternary(left, then, self.expression())
                continue
            power = PRECEDENCE.get(token.text)
            if power is None or power < minimum:
                break
            self.advance()
            left = Binary(token.text, left, self.expression(power + 1))
        return left

    def unary(self) -> Expr:
        token = self.current
        if token.kind is Kind.OP and token.text in ("-", "!", "+", "~"):
            self.advance()
            operand = self.unary()
            return operand if token.text == "+" else Unary(token.text, operand)
        return self.postfix(self.primary())

    def postfix(self, value: Expr) -> Expr:
        while True:
            if self.accept(Kind.PUNCT, "["):
                index = self.expression()
                self.expect(Kind.PUNCT, "]")
                value = Index(value, index)
            elif self.current.is_(Kind.OP, "."):
                self.advance()
                value = Member(value, self.expect(Kind.NAME).text)
            else:
                return value

    def primary(self) -> Expr:
        token = self.current

        if token.kind is Kind.NUMBER:
            self.advance()
            kind = "float" if ("." in token.text or "e" in token.text.lower()) else "int"
            return Literal(token.text, kind)

        if token.kind is Kind.STRING:
            self.advance()
            return Literal(token.text, "string")

        if token.kind is Kind.ATTRIB:
            self.advance()
            return Attribute(token.text, token.prefix, token.is_array)

        if token.kind is Kind.HSCRIPT:
            self.advance()
            return Hscript(token.text)

        if token.is_(Kind.PUNCT, "("):
            # `(float)i` is a C-style cast, not a parenthesised expression.
            if self.peek().kind is Kind.TYPE and self.peek(2).is_(Kind.PUNCT, ")"):
                self.advance()
                to = self.advance().text
                self.advance()
                return Cast(to, self.unary())
            self.advance()
            value = self.expression()
            self.expect(Kind.PUNCT, ")")
            return value

        if token.is_(Kind.PUNCT, "{"):
            # In expression position a brace is a vector or array literal.
            self.advance()
            items: list[Expr] = []
            while not self.current.is_(Kind.PUNCT, "}"):
                items.append(self.expression())
                if not self.accept(Kind.PUNCT, ","):
                    break
            self.expect(Kind.PUNCT, "}")
            return VectorLiteral(items)

        if token.kind is Kind.TYPE:
            self.advance()
            # `vector(x)` and `float[](...)` are casts, not calls.
            if self.accept(Kind.PUNCT, "["):
                self.expect(Kind.PUNCT, "]")
                self.expect(Kind.PUNCT, "(")
                value = self.expression()
                self.expect(Kind.PUNCT, ")")
                return Cast(f"{token.text}[]", value)
            self.expect(Kind.PUNCT, "(")
            args = self.arguments()
            return Cast(token.text, args[0]) if len(args) == 1 else Call(token.text, args)

        if token.kind is Kind.NAME:
            self.advance()
            if self.accept(Kind.PUNCT, "("):
                return Call(token.text, self.arguments())
            return Name(token.text)

        raise ParseError(f"unexpected {token.text!r}", token)

    def arguments(self) -> list[Expr]:
        """Arguments after the opening bracket has been consumed."""
        args: list[Expr] = []
        while not self.current.is_(Kind.PUNCT, ")"):
            args.append(self.expression())
            if not self.accept(Kind.PUNCT, ","):
                break
        self.expect(Kind.PUNCT, ")")
        return args


def parse(source: str) -> list[Statement]:
    return Parser(source).parse()
