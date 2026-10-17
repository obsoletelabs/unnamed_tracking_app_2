"""Bounded CommonMark subset rendered to a safe node tree, never to HTML strings.

Raw HTML is plain text, images are not rendered, and a link survives only when its target is a
public HTTPS URL or an application path. Clients draw the tree with ordinary text nodes, so no
markup from a producer can reach a DOM. Parsing is a single linear scan per line with explicit
depth and node limits, so adversarial input cannot trigger backtracking or deep recursion.
"""

import re
from dataclasses import dataclass
from typing import Any

from src.features.notification_safety import url_is_safe

Node = dict[str, Any]

MAX_NODES = 1500
MAX_DEPTH = 4
MAX_LIST_ITEMS = 100
# Every inline construct is searched for within a fixed window, so scanning stays linear.
_WINDOW = 600
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_BULLET = re.compile(r"^[ \t]{0,3}[-*+][ \t]+(.*)$")
_ORDERED = re.compile(r"^[ \t]{0,3}[0-9]{1,9}[.)][ \t]+(.*)$")
_RULE = re.compile(r"^[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?(.*)$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_ESCAPABLE = "\\`*_{}[]()#+-.!<>~|"


@dataclass
class _Budget:
    nodes: int = 0


def _spend(budget: _Budget) -> bool:
    budget.nodes += 1
    return budget.nodes <= MAX_NODES


def _text(value: str) -> Node:
    return {"t": "text", "v": value}


def _find_close(text: str, start: int, marker: str) -> int:
    """Index of the closing emphasis marker, ignoring escapes; -1 when absent."""
    index, end = start, min(len(text), start + _WINDOW)
    while index < end:
        if text[index] == "\\":
            index += 2
            continue
        if text.startswith(marker, index) and index > start:
            return index
        index += 1
    return -1


def _link_end(text: str, start: int) -> tuple[int, int] | None:
    """For `[label](target)` starting at `[`, return (label_end, target_end) indexes."""
    depth, index, end = 0, start, min(len(text), start + _WINDOW)
    while index < end:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                break
        index += 1
    else:
        return None
    if not text.startswith("(", index + 1):
        return None
    close = text.find(")", index + 2, index + 2 + _WINDOW)
    return (index, close) if close != -1 else None


class _Inline:
    """One linear left-to-right scan; constructs are matched within a fixed window."""

    def __init__(self, text: str, budget: _Budget, depth: int) -> None:
        self.text, self.budget, self.depth = text, budget, depth
        self.nodes: list[Node] = []
        self.buffer: list[str] = []
        self.index = 0

    def flush(self) -> None:
        if self.buffer:
            if _spend(self.budget):
                self.nodes.append(_text("".join(self.buffer)))
            self.buffer.clear()

    def code(self) -> bool:
        close = self.text.find("`", self.index + 1, self.index + 1 + _WINDOW)
        if close <= self.index + 1:
            return False
        self.flush()
        if _spend(self.budget):
            self.nodes.append({"t": "code", "v": self.text[self.index + 1 : close]})
        self.index = close + 1
        return True

    def emphasis(self, char: str) -> bool:
        strong = self.text.startswith(char * 2, self.index)
        marker = char * 2 if strong else char
        close = _find_close(self.text, self.index + len(marker), marker)
        inner = self.text[self.index + len(marker) : close] if close != -1 else ""
        if close == -1 or not inner.strip() or inner[0].isspace():
            return False
        self.flush()
        if _spend(self.budget):
            kind = "strong" if strong else "em"
            self.nodes.append({"t": kind, "c": _inline(inner, self.budget, self.depth + 1)})
        self.index = close + len(marker)
        return True

    def link(self, image: bool) -> bool:
        bounds = _link_end(self.text, self.index + (1 if image else 0))
        if bounds is None:
            return False
        label_end, target_end = bounds
        label = self.text[self.index + (2 if image else 1) : label_end]
        target = self.text[label_end + 2 : target_end].strip().split(" ", 1)[0].strip("<>")
        self.flush()
        children = [] if image else _inline(label, self.budget, self.depth + 1)
        if image:
            # Embedded images are never rendered; the alt text stays readable.
            self.buffer.append(label)
        elif url_is_safe(target) and _spend(self.budget):
            self.nodes.append({"t": "link", "href": target, "c": children})
        else:
            self.nodes.extend(children)
        self.index = target_end + 1
        return True

    def construct(self, char: str) -> bool:
        """Try the inline construct starting at the current character."""
        if self.depth >= MAX_DEPTH and char != "`":
            return False
        if char == "`":
            return self.code()
        if char in "*_":
            return self.emphasis(char)
        if char == "[":
            return self.link(False)
        return char == "!" and self.text.startswith("[", self.index + 1) and self.link(True)

    def run(self) -> list[Node]:
        text = self.text
        while self.index < len(text) and self.budget.nodes <= MAX_NODES:
            char = text[self.index]
            if char == "\\" and self.index + 1 < len(text) and text[self.index + 1] in _ESCAPABLE:
                self.buffer.append(text[self.index + 1])
                self.index += 2
            elif not self.construct(char):
                self.buffer.append(char)
                self.index += 1
        self.flush()
        return self.nodes


def _inline(text: str, budget: _Budget, depth: int = 0) -> list[Node]:
    return _Inline(text, budget, depth).run()


def _paragraph_lines(lines: list[str], budget: _Budget) -> Node:
    children: list[Node] = []
    for position, line in enumerate(lines):
        hard = line.endswith("  ") or line.endswith("\\")
        children.extend(_inline(line.rstrip("\\").strip(), budget))
        if position < len(lines) - 1:
            children.append({"t": "br"} if hard else _text(" "))
    return {"t": "p", "c": children}


def _starts_block(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _BULLET.match(line)
        or _ORDERED.match(line)
        or _RULE.match(line)
        or _QUOTE.match(line)
        or _FENCE.match(line)
    )


def _collect_list(
    lines: list[str], start: int, pattern: re.Pattern[str], budget: _Budget
) -> tuple[Node, int]:
    items: list[Node] = []
    index = start
    while index < len(lines) and len(items) < MAX_LIST_ITEMS:
        match = pattern.match(lines[index])
        if not match:
            break
        items.append({"t": "li", "c": _inline(match.group(1).strip(), budget)})
        index += 1
    kind = "ol" if pattern is _ORDERED else "ul"
    return {"t": kind, "c": items}, index


def _fenced(lines: list[str], index: int, marker: str) -> tuple[Node, int]:
    code: list[str] = []
    index += 1
    while index < len(lines) and not lines[index].lstrip().startswith(marker):
        code.append(lines[index])
        index += 1
    return {"t": "pre", "v": "\n".join(code)}, index + 1


def _quote(lines: list[str], index: int, budget: _Budget) -> tuple[Node, int]:
    quoted: list[str] = []
    while index < len(lines) and (match := _QUOTE.match(lines[index])):
        quoted.append(match.group(1))
        index += 1
    return {"t": "quote", "c": [_paragraph_lines(quoted, budget)]}, index


def _paragraph(lines: list[str], index: int, budget: _Budget) -> tuple[Node, int]:
    paragraph = [lines[index]]
    index += 1
    while index < len(lines) and lines[index].strip() and not _starts_block(lines[index]):
        paragraph.append(lines[index])
        index += 1
    return _paragraph_lines(paragraph, budget), index


def _block(lines: list[str], index: int, budget: _Budget) -> tuple[Node, int]:
    line = lines[index]
    if fence := _FENCE.match(line):
        return _fenced(lines, index, fence.group(1))
    if heading := _HEADING.match(line):
        level = min(len(heading.group(1)) + 2, 5)
        return {"t": "h", "level": level, "c": _inline(heading.group(2), budget)}, index + 1
    if _RULE.match(line):
        return {"t": "hr"}, index + 1
    if _QUOTE.match(line):
        return _quote(lines, index, budget)
    for pattern in (_BULLET, _ORDERED):
        if pattern.match(line):
            return _collect_list(lines, index, pattern, budget)
    return _paragraph(lines, index, budget)


def render_markdown(source: str) -> list[Node]:
    """Block nodes: p, h (level 3-5), ul, ol, quote, pre, hr. Always finite and escaped."""
    budget = _Budget()
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Node] = []
    index = 0
    while index < len(lines) and budget.nodes <= MAX_NODES:
        if not lines[index].strip():
            index += 1
            continue
        block, index = _block(lines, index, budget)
        blocks.append(block)
    return blocks


def _flatten(node: Node, parts: list[str]) -> None:
    kind = node["t"]
    if kind in {"text", "code"}:
        parts.append(node["v"])
    elif kind in {"br", "hr"}:
        parts.append("\n")
    elif kind == "pre":
        parts.append(node["v"] + "\n\n")
    elif kind == "link":
        start = len(parts)
        for child in node["c"]:
            _flatten(child, parts)
        if node["href"] not in "".join(parts[start:]):
            parts.append(f" ({node['href']})")
    elif kind in {"ul", "ol"}:
        for number, item in enumerate(node["c"], 1):
            parts.append(f"{number}. " if kind == "ol" else "- ")
            for child in item["c"]:
                _flatten(child, parts)
            parts.append("\n")
        parts.append("\n")
    else:
        for child in node.get("c", []):
            _flatten(child, parts)
        if kind in {"p", "h", "quote"}:
            parts.append("\n\n")


def plain_text(nodes: list[Node]) -> str:
    """Readable plain text for providers without Markdown or HTML support."""
    parts: list[str] = []
    for node in nodes:
        _flatten(node, parts)
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()
