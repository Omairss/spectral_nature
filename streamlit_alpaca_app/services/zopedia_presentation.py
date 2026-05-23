from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_TABLE_SEPARATOR_RE = re.compile(r":?-{3,}:?")


@dataclass(frozen=True)
class MarkdownTable:
    columns: list[str]
    rows: list[dict[str, str]]


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _pipe_cells(line: str) -> list[str]:
    text = str(line or "").strip()
    if not text or "|" not in text:
        return []
    return [cell.strip() for cell in text.strip("|").split("|")]


def _is_separator_cells(cells: list[str]) -> bool:
    nonempty = [cell.replace(" ", "") for cell in cells if cell.strip()]
    return bool(nonempty) and all(_TABLE_SEPARATOR_RE.fullmatch(cell) for cell in nonempty)


def _make_pipe_row(cells: list[str]) -> str:
    clean_cells = [_clean_text(cell) for cell in cells]
    return "| " + " | ".join(clean_cells) + " |"


def _make_separator_row(count: int) -> str:
    return "| " + " | ".join("---" for _ in range(max(int(count or 0), 1))) + " |"


def _split_long_answer_paragraph(paragraph: str, *, max_chars: int = 520) -> list[str]:
    text = " ".join(str(paragraph or "").strip().split())
    if len(text) <= max_chars:
        return [text] if text else []
    if re.match(r"^\s*(```|#{1,6}\s|[-*]\s|\d+\.\s)", paragraph):
        return [paragraph.strip()]
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+(?=(?:\*\*)?[A-Z0-9$])", text)
        if item.strip()
    ]
    if len(sentences) <= 1:
        return [text[i : i + max_chars].strip() for i in range(0, len(text), max_chars)]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _looks_like_table_block(block: str) -> bool:
    lines = [line.strip() for line in str(block or "").splitlines() if line.strip()]
    pipe_lines = [line for line in lines if "|" in line]
    if len(pipe_lines) >= 2 and any(_is_separator_cells(_pipe_cells(line)) for line in pipe_lines):
        return True
    if len(pipe_lines) == 1:
        cells = _pipe_cells(pipe_lines[0])
        return len(cells) >= 6 and any(_TABLE_SEPARATOR_RE.fullmatch(cell.replace(" ", "")) for cell in cells)
    return False


def _normalize_table_block(block: str, *, heading_header_cells: list[str] | None = None) -> str:
    lines = [line.rstrip() for line in str(block or "").splitlines() if line.strip()]
    if not lines:
        return ""
    header_prefix = [_clean_text(cell) for cell in list(heading_header_cells or []) if _clean_text(cell)]
    if header_prefix and "|" in lines[0]:
        header_cells = header_prefix + _pipe_cells(lines[0])
        lines[0] = _make_pipe_row(header_cells)
        if len(lines) > 1 and _is_separator_cells(_pipe_cells(lines[1])):
            lines[1] = _make_separator_row(len(header_cells))
    return "\n".join(lines)


def prepare_answer_markdown_blocks(answer: str) -> list[str]:
    """Split answer markdown into renderable blocks without flattening tables.

    The old renderer treated any block starting with a heading as one large
    heading, so a heading immediately followed by a Markdown table became a
    broken one-line blob. This keeps only the first line as the heading and
    preserves the following table/body as its own block.
    """
    text = str(answer or "").strip()
    if not text:
        return []
    text = re.sub(r"\s+(#{1,6}\s+)", r"\n\n\1", text)
    text = re.sub(r"(?<!\n)\s+([-*]\s+(?:\*\*)?[A-Z0-9$])", r"\n\n\1", text)
    raw_blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    blocks: list[str] = []

    def _append_body(body: str, *, heading_header_cells: list[str] | None = None) -> None:
        body = str(body or "").strip()
        if not body:
            return
        if body.startswith("```") or _looks_like_table_block(body):
            blocks.append(_normalize_table_block(body, heading_header_cells=heading_header_cells))
            return
        blocks.extend(_split_long_answer_paragraph(body))

    for block in raw_blocks:
        if block.startswith("```"):
            blocks.append(block)
            continue

        lines = block.splitlines()
        first_line = lines[0].strip() if lines else ""
        heading_match = re.match(r"^(#{1,6}\s+)(.+)$", first_line)
        if heading_match:
            heading_prefix = heading_match.group(1)
            heading_body = _clean_text(heading_match.group(2))
            heading_header_cells: list[str] = []
            if "|" in heading_body:
                cells = [_clean_text(cell) for cell in _pipe_cells(heading_body) if _clean_text(cell)]
                if cells:
                    heading_body = cells[0]
                    heading_header_cells = cells[1:]
            if len(heading_body) <= 88:
                blocks.append(f"{heading_prefix}{heading_body}")
            else:
                words = heading_body.split()
                heading_words: list[str] = []
                if words and words[0].strip(":").lower() in {"takeaway", "verdict", "conclusion"}:
                    heading_words = [words[0].strip(":")]
                else:
                    for word_idx, word in enumerate(words):
                        clean_word = re.sub(r"^[^A-Za-z0-9$]+|[^A-Za-z0-9$]+$", "", word)
                        prev_word = (
                            re.sub(r"^[^A-Za-z0-9$]+|[^A-Za-z0-9$]+$", "", words[word_idx - 1])
                            if word_idx > 0
                            else ""
                        )
                        if word == "-" and len(heading_words) >= 2:
                            break
                        if word_idx >= 3 and clean_word[:1].isupper() and prev_word[:1].islower():
                            break
                        if word_idx >= 9:
                            break
                        heading_words.append(word)
                if not heading_words:
                    heading_words = words[: min(len(words), 6)]
                heading = " ".join(heading_words).strip()
                rest = " ".join(words[len(heading_words) :]).strip()
                blocks.append(f"{heading_prefix}{heading}")
                _append_body(rest)

            remainder = "\n".join(lines[1:]).strip()
            _append_body(remainder, heading_header_cells=heading_header_cells)
            continue

        _append_body(block)
    return [block for block in blocks if str(block or "").strip()]


def parse_markdown_table(block: str) -> MarkdownTable | None:
    """Parse a Markdown pipe table, including common one-line table collapse."""
    text = str(block or "").strip()
    if not text or "|" not in text:
        return None
    lines = [line.strip() for line in text.splitlines() if "|" in line and line.strip()]
    if not lines:
        return None

    separator_index = -1
    for idx, line in enumerate(lines):
        if _is_separator_cells(_pipe_cells(line)):
            separator_index = idx
            break

    if separator_index > 0:
        columns = [_clean_text(cell) for cell in _pipe_cells(lines[separator_index - 1]) if _clean_text(cell)]
        data_lines = lines[separator_index + 1 :]
        if not columns or not data_lines:
            return None
        rows: list[dict[str, str]] = []
        for line in data_lines:
            cells = [_clean_text(cell) for cell in _pipe_cells(line)]
            if not cells or _is_separator_cells(cells):
                continue
            if len(cells) < len(columns):
                cells.extend("" for _ in range(len(columns) - len(cells)))
            row = {columns[col_idx]: cells[col_idx] for col_idx in range(len(columns))}
            rows.append(row)
        return MarkdownTable(columns=columns, rows=rows) if rows else None

    cells = [_clean_text(cell) for cell in _pipe_cells(" ".join(lines)) if _clean_text(cell)]
    if len(cells) < 6:
        return None
    sep_start = next(
        (idx for idx, cell in enumerate(cells) if _TABLE_SEPARATOR_RE.fullmatch(cell.replace(" ", ""))),
        -1,
    )
    if sep_start <= 0:
        return None
    sep_end = sep_start
    while sep_end < len(cells) and _TABLE_SEPARATOR_RE.fullmatch(cells[sep_end].replace(" ", "")):
        sep_end += 1
    width = sep_end - sep_start
    if width <= 0:
        return None
    columns = cells[max(0, sep_start - width) : sep_start]
    if len(columns) != width or not columns:
        return None
    data = cells[sep_end:]
    rows = []
    for start in range(0, len(data), width):
        row_cells = data[start : start + width]
        if len(row_cells) != width:
            continue
        rows.append({columns[col_idx]: row_cells[col_idx] for col_idx in range(width)})
    return MarkdownTable(columns=columns, rows=rows) if rows else None


def trace_step_title(step: dict[str, Any], *, index: int, tool_label: str = "") -> str:
    step_type = str(step.get("type") or "").strip()
    if step_type == "reasoning":
        return f"{index}. Reasoning"
    if step_type == "model_reasoning_trace":
        return f"{index}. Model reasoning"
    if step_type == "tool_start":
        label = _clean_text(tool_label or step.get("tool_name") or "Data source")
        return f"{index}. Checking {label}"
    if step_type == "tool_complete":
        label = _clean_text(tool_label or step.get("tool_name") or "")
        return f"{index}. Evidence from {label}" if label else f"{index}. Evidence added"
    return f"{index}. Progress"


def trace_step_body(step: dict[str, Any]) -> str:
    step_type = str(step.get("type") or "").strip()
    if step_type in {"reasoning", "message"}:
        return str(step.get("text") or "").strip()
    if step_type == "model_reasoning_trace":
        return str(step.get("text") or "").strip()
    if step_type == "tool_start":
        args_text = str(step.get("args_text") or "").strip()
        return f"Arguments: {args_text}" if args_text and args_text != "{}" else ""
    if step_type == "tool_complete":
        return str(step.get("preview") or "").strip()
    return ""
