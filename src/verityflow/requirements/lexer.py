from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TokenKind(StrEnum):
    WORD = "word"
    NUMBER = "number"
    STRING = "string"
    OP = "op"
    LPAREN = "lparen"
    RPAREN = "rparen"
    COMMA = "comma"
    COLON = "colon"
    PERIOD = "period"
    EOF = "eof"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    column: int


class TokenizeError(ValueError):
    def __init__(self, message: str, column: int) -> None:
        super().__init__(message)
        self.column = column


_MULTI_OPS = ("->", ">=", "<=", "==", "!=")
_SINGLE_OPS = {">", "<", "="}
_PUNCTUATION = {
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    ",": TokenKind.COMMA,
    ":": TokenKind.COLON,
    ".": TokenKind.PERIOD,
}


def tokenize(text: str, base_column: int = 1) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        column = base_column + index
        if char.isspace():
            index += 1
            continue
        if char in _PUNCTUATION:
            tokens.append(Token(_PUNCTUATION[char], char, column))
            index += 1
            continue
        matched = next((op for op in _MULTI_OPS if text.startswith(op, index)), None)
        if matched is not None and matched != "->":
            tokens.append(Token(TokenKind.OP, matched, column))
            index += len(matched)
            continue
        if text.startswith("->", index):
            tokens.append(Token(TokenKind.OP, "->", column))
            index += 2
            continue
        if char in _SINGLE_OPS:
            tokens.append(Token(TokenKind.OP, char, column))
            index += 1
            continue
        if char == "!":
            if index + 1 < length and text[index + 1] == "=":
                tokens.append(Token(TokenKind.OP, "!=", column))
                index += 2
                continue
            raise TokenizeError("unexpected '!' (did you mean '!='?)", column)
        if char in "\"'":
            quote = char
            index += 1
            buffer: list[str] = []
            while index < length and text[index] != quote:
                if text[index] == "\\" and index + 1 < length:
                    buffer.append(text[index + 1])
                    index += 2
                    continue
                buffer.append(text[index])
                index += 1
            if index >= length:
                raise TokenizeError("unterminated string literal", column)
            index += 1
            tokens.append(Token(TokenKind.STRING, "".join(buffer), column))
            continue
        start = index
        if char.isdigit() or (char == "-" and index + 1 < length and text[index + 1].isdigit()):
            index += 1
            while index < length and (text[index].isdigit() or text[index] == "."):
                index += 1
            tokens.append(Token(TokenKind.NUMBER, text[start:index], column))
            continue
        if char.isalpha() or char == "_":
            index += 1
            while index < length and (text[index].isalnum() or text[index] == "_"):
                index += 1
            tokens.append(Token(TokenKind.WORD, text[start:index], column))
            continue
        raise TokenizeError(f"unexpected character {char!r}", column)
    tokens.append(Token(TokenKind.EOF, "", base_column + length))
    return tokens
