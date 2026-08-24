"""Pure-Python HTML-to-PDF renderer used by LandValue360.

The renderer deliberately avoids browser, GTK, Cairo and system DLL dependencies.
It turns the governed report HTML into a compact document model and paints A4
pages with Pillow.  Arabic shaping and bidirectional layout are delegated to
Pillow's bundled RAQM support when available.  No font files are distributed;
Windows system fonts are discovered at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
import os
from pathlib import Path
import re
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, features

from .arabic_text import contains_arabic as _contains_arabic, visual_rtl


@dataclass(slots=True)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


class _DomParser(HTMLParser):
    _VOID = {"br", "hr", "img", "meta", "link", "input", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag.lower() not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


@dataclass(frozen=True, slots=True)
class Block:
    kind: str
    payload: Any
    level: int = 0
    tone: str = ""


def _text(node: Node | str) -> str:
    if isinstance(node, str):
        return node
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, Node) and child.tag == "br":
            parts.append("\n")
        else:
            value = _text(child)
            if value:
                parts.append(value)
    # HTML report templates frequently place labels and numeric values in
    # adjacent spans without literal whitespace.  Joining semantic children
    # with a space prevents merged output such as ``USD29,840,400`` while
    # preserving explicit line breaks.
    joined = " ".join(parts)
    joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
    joined = re.sub(r" *\n *", "\n", joined)
    return joined.strip()


def _descendants(node: Node, tag: str | None = None) -> Iterable[Node]:
    for child in node.children:
        if isinstance(child, Node):
            if tag is None or child.tag == tag:
                yield child
            yield from _descendants(child, tag)


def _find_first(node: Node, tag: str) -> Node | None:
    if node.tag == tag:
        return node
    return next(_descendants(node, tag), None)


def _table_rows(table: Node) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in _descendants(table, "tr"):
        cells: list[str] = []
        for child in tr.children:
            if isinstance(child, Node) and child.tag in {"th", "td"}:
                cells.append(_text(child))
        if cells:
            rows.append(cells)
    return rows


def _card_items(node: Node) -> list[str]:
    items: list[str] = []
    for child in node.children:
        if not isinstance(child, Node):
            continue
        if child.tag == "div" or "metric" in " ".join(child.classes):
            value = _text(child)
            if value:
                items.append(value)
    return items


def _blocks(node: Node, *, first_page_state: list[bool] | None = None) -> list[Block]:
    if first_page_state is None:
        first_page_state = [True]
    result: list[Block] = []
    if node.tag in {"style", "script", "noscript", "svg", "head"}:
        return result
    classes = node.classes
    forced_section_page = node.tag == "section" and node.attrs.get("id") in {"decision", "range", "resilience", "conclusion"}
    if (node.tag in {"section", "header"} and ({"page", "cover"} & classes)) or forced_section_page:
        if not first_page_state[0]:
            result.append(Block("page_break", None))
        first_page_state[0] = False
    if node.tag in {"h1", "h2", "h3", "h4"}:
        value = _text(node)
        if value:
            result.append(Block("heading", value, int(node.tag[1])))
        return result
    if node.tag == "table":
        rows = _table_rows(node)
        if rows:
            result.append(Block("table", rows, tone="compact" if "compact" in " ".join(classes) else ""))
        return result
    if "metric-grid" in classes or "metrics" in classes or "cover-summary" in classes or "stat-grid" in classes:
        items = _card_items(node)
        if items:
            result.append(Block("cards", items))
        return result
    if classes & {"callout", "decision-box", "warning", "alert", "cover-disclaimer"}:
        value = _text(node)
        if value:
            tone = "warning" if classes & {"warning", "alert--warning", "callout--warning"} else "danger" if classes & {"alert--danger", "decision-box--danger"} else "info"
            result.append(Block("callout", value, tone=tone))
        return result
    if node.tag in {"ul", "ol"}:
        for item in _descendants(node, "li"):
            value = _text(item)
            if value:
                result.append(Block("paragraph", f"• {value}"))
        return result
    if node.tag in {"p", "dt", "dd"}:
        value = _text(node)
        if value:
            result.append(Block("paragraph", value))
        return result
    if node.tag == "hr":
        result.append(Block("rule", None))
        return result
    for child in node.children:
        if isinstance(child, Node):
            result.extend(_blocks(child, first_page_state=first_page_state))
    return result


def _system_font_candidates(*, bold: bool = False) -> list[Path]:
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if bold:
        names = ["arialbd.ttf", "tahomabd.ttf", "segoeuib.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf"]
    else:
        names = ["arial.ttf", "tahoma.ttf", "segoeui.ttf", "calibri.ttf", "DejaVuSans.ttf"]
    roots = [win, Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/liberation2"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [root / name for root in roots for name in names]


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    for candidate in _system_font_candidates(bold=bold):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size, layout_engine=ImageFont.Layout.RAQM if _raqm_enabled() else ImageFont.Layout.BASIC)
    return ImageFont.load_default(size=size)


def _raqm_enabled() -> bool:
    forced_basic = str(os.environ.get("LV360_PDF_FORCE_BASIC_ARABIC", "")).strip().lower() in {"1", "true", "yes", "on"}
    return bool(features.check("raqm")) and not forced_basic


def _display_text(text: str, *, rtl: bool) -> tuple[str, bool]:
    """Return display text and whether Pillow should apply RTL layout itself."""
    value = str(text)
    if rtl and _contains_arabic(value) and not _raqm_enabled():
        return visual_rtl(value), False
    return value, bool(rtl and _contains_arabic(value) and _raqm_enabled())


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, *, rtl: bool) -> float:
    display, native_rtl = _display_text(text, rtl=rtl)
    try:
        if native_rtl:
            return float(draw.textlength(display, font=font, direction="rtl", language="ar"))
        return float(draw.textlength(display, font=font))
    except Exception:
        box = draw.textbbox((0, 0), display, font=font)
        return float(box[2] - box[0])


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, *, rtl: bool) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if _measure(draw, candidate, font, rtl=rtl) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


class _Painter:
    def __init__(self, *, landscape: bool, rtl: bool, title: str) -> None:
        self.landscape = landscape
        self.rtl = rtl
        self.title = title
        self.width, self.height = ((1684, 1190) if landscape else (1190, 1684))
        self.margin_x = 70
        self.margin_top = 80
        self.margin_bottom = 80
        self.content_width = self.width - self.margin_x * 2
        self.pages: list[Image.Image] = []
        self.image: Image.Image
        self.draw: ImageDraw.ImageDraw
        self.y = 0
        self.page_number = 0
        self.new_page()

    def new_page(self) -> None:
        self.image = Image.new("RGB", (self.width, self.height), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.page_number += 1
        self.y = self.margin_top
        header_font = _font(16, bold=True)
        footer_font = _font(13)
        self._draw_text(self.title, self.margin_x, 25, self.content_width, header_font, fill="#173f4a", rtl=self.rtl, line_spacing=3)
        self.draw.line((self.margin_x, 60, self.width - self.margin_x, 60), fill="#b9c9cc", width=2)
        footer = f"LandValue360 - {self.page_number}"
        self._draw_text(footer, self.margin_x, self.height - 50, self.content_width, footer_font, fill="#60777b", rtl=False)
        if self.pages:
            pass
        self.pages.append(self.image)

    def _draw_text(self, text: str, x: int, y: int, width: int, font: ImageFont.ImageFont, *, fill: str = "#122e36", rtl: bool | None = None, line_spacing: int = 6) -> int:
        actual_rtl = self.rtl if rtl is None else rtl
        lines = _wrap(self.draw, text, font, width, rtl=actual_rtl)
        line_h = int(font.size * 1.35) if hasattr(font, "size") else 22
        for index, line in enumerate(lines):
            yy = y + index * (line_h + line_spacing)
            display, native_rtl = _display_text(line, rtl=actual_rtl)
            xx = x + width if actual_rtl else x
            anchor = "ra" if actual_rtl else "la"
            try:
                kwargs = {"font": font, "fill": fill, "anchor": anchor}
                if native_rtl:
                    kwargs.update({"direction": "rtl", "language": "ar"})
                self.draw.text((xx, yy), display, **kwargs)
            except Exception:
                measured = _measure(self.draw, line, font, rtl=actual_rtl)
                fallback_x = x + max(0, width - int(measured)) if actual_rtl else x
                self.draw.text((fallback_x, yy), display, font=font, fill=fill)
        return len(lines) * (line_h + line_spacing)

    def ensure(self, required: int) -> None:
        if self.y + required > self.height - self.margin_bottom:
            self.new_page()

    def heading(self, text: str, level: int) -> None:
        size = {1: 38, 2: 30, 3: 23, 4: 19}.get(level, 20)
        font = _font(size, bold=True)
        temp = _wrap(self.draw, text, font, self.content_width, rtl=self.rtl)
        required = len(temp) * int(size * 1.5) + 30
        self.ensure(required)
        used = self._draw_text(text, self.margin_x, self.y, self.content_width, font, fill="#0f3440", rtl=self.rtl, line_spacing=4)
        self.y += used + 18
        self.draw.line((self.margin_x, self.y, self.width - self.margin_x, self.y), fill="#1d5e5a", width=3 if level <= 2 else 1)
        self.y += 18

    def paragraph(self, text: str) -> None:
        font = _font(18)
        lines = _wrap(self.draw, text, font, self.content_width, rtl=self.rtl or _contains_arabic(text))
        required = len(lines) * 31 + 14
        self.ensure(required)
        used = self._draw_text(text, self.margin_x, self.y, self.content_width, font, fill="#324d54", rtl=self.rtl or _contains_arabic(text), line_spacing=4)
        self.y += used + 14

    def callout(self, text: str, tone: str) -> None:
        font = _font(18, bold=False)
        inner = self.content_width - 40
        lines = _wrap(self.draw, text, font, inner, rtl=self.rtl or _contains_arabic(text))
        height = max(70, len(lines) * 31 + 36)
        self.ensure(height + 20)
        colors = {"warning": ("#fff3cf", "#ad7600"), "danger": ("#ffe8e4", "#a53b31"), "info": ("#e8f4f2", "#176a62")}
        bg, border = colors.get(tone, colors["info"])
        self.draw.rounded_rectangle((self.margin_x, self.y, self.width - self.margin_x, self.y + height), radius=16, fill=bg, outline=border, width=3)
        self._draw_text(text, self.margin_x + 20, self.y + 16, inner, font, fill="#243f46", rtl=self.rtl or _contains_arabic(text), line_spacing=4)
        self.y += height + 20

    def cards(self, items: list[str]) -> None:
        cols = 3 if self.landscape else 2
        gap = 16
        card_w = int((self.content_width - gap * (cols - 1)) / cols)
        font = _font(17)
        bold = _font(20, bold=True)
        wrapped = [_wrap(self.draw, item, font, card_w - 30, rtl=self.rtl or _contains_arabic(item)) for item in items]
        row_heights: list[int] = []
        for start in range(0, len(items), cols):
            count = max((len(wrapped[i]) for i in range(start, min(start + cols, len(items)))), default=1)
            row_heights.append(max(100, count * 28 + 38))
        total = sum(row_heights) + gap * max(0, len(row_heights) - 1)
        self.ensure(min(total, self.height - self.margin_top - self.margin_bottom))
        index = 0
        yy = self.y
        for rh in row_heights:
            if yy + rh > self.height - self.margin_bottom:
                self.new_page(); yy = self.y
            for col in range(cols):
                if index >= len(items): break
                xx = self.margin_x + col * (card_w + gap)
                self.draw.rounded_rectangle((xx, yy, xx + card_w, yy + rh), radius=14, fill="#f0f6f6", outline="#c8d7d9", width=2)
                lines = wrapped[index]
                for line_index, line in enumerate(lines):
                    use_font = bold if line_index == len(lines) - 1 and any(ch.isdigit() for ch in line) else font
                    self._draw_text(line, xx + 15, yy + 18 + line_index * 30, card_w - 30, use_font, rtl=self.rtl or _contains_arabic(line), line_spacing=0)
                index += 1
            yy += rh + gap
        self.y = yy + 8

    def table(self, rows: list[list[str]], *, compact: bool = False) -> None:
        if not rows: return
        column_count = max(len(row) for row in rows)
        normalized = [row + [""] * (column_count - len(row)) for row in rows]
        # Weight narrative columns higher than numeric/status columns.
        max_lengths = [max((len(str(row[col])) for row in normalized), default=1) for col in range(column_count)]
        weights = [min(4.5, max(0.75, length / 18)) for length in max_lengths]
        total_weight = sum(weights)
        widths = [max(75, int(self.content_width * weight / total_weight)) for weight in weights]
        scale = self.content_width / sum(widths)
        widths = [int(width * scale) for width in widths]
        widths[-1] += self.content_width - sum(widths)
        font = _font(13 if compact or column_count > 6 else 15)
        header_font = _font(14 if compact else 15, bold=True)
        padding = 9

        def row_height(row: list[str], use_header: bool = False) -> int:
            f = header_font if use_header else font
            counts = [len(_wrap(self.draw, cell, f, max(20, widths[i] - padding * 2), rtl=self.rtl or _contains_arabic(cell))) for i, cell in enumerate(row)]
            return max(38, max(counts, default=1) * (22 if compact else 25) + padding * 2)

        header = normalized[0]
        header_h = row_height(header, True)
        self.ensure(header_h + 50)

        def draw_row(row: list[str], height: int, *, header_row: bool = False) -> None:
            x = self.margin_x
            fill = "#194c64" if header_row else "#ffffff"
            color = "#ffffff" if header_row else "#203d46"
            f = header_font if header_row else font
            for col, cell in enumerate(row):
                self.draw.rectangle((x, self.y, x + widths[col], self.y + height), fill=fill, outline="#b9c9cc", width=1)
                lines = _wrap(self.draw, cell, f, max(20, widths[col] - padding * 2), rtl=self.rtl or _contains_arabic(cell))
                line_h = 22 if compact else 25
                text_y = self.y + max(padding, (height - len(lines) * line_h) // 2)
                self._draw_text("\n".join(lines), x + padding, text_y, widths[col] - padding * 2, f, fill=color, rtl=self.rtl or _contains_arabic(cell), line_spacing=0)
                x += widths[col]
            self.y += height

        draw_row(header, header_h, header_row=True)
        for row in normalized[1:]:
            rh = row_height(row)
            if self.y + rh > self.height - self.margin_bottom:
                self.new_page()
                draw_row(header, header_h, header_row=True)
            draw_row(row, rh)
        self.y += 20

    def rule(self) -> None:
        self.ensure(20)
        self.draw.line((self.margin_x, self.y, self.width - self.margin_x, self.y), fill="#b9c9cc", width=2)
        self.y += 20

    def finish(self) -> bytes:
        buffer = BytesIO()
        self.pages[0].save(buffer, format="PDF", resolution=144.0, save_all=True, append_images=self.pages[1:])
        return buffer.getvalue()


def render_html_pdf(html: str) -> bytes:
    parser = _DomParser()
    parser.feed(html)
    html_node = _find_first(parser.root, "html") or parser.root
    language = (html_node.attrs.get("lang") or "en").lower()
    rtl = (html_node.attrs.get("dir") or "").lower() == "rtl" or language == "ar"
    body = _find_first(html_node, "body") or html_node
    title_node = _find_first(html_node, "title")
    title = _text(title_node) if title_node else "LandValue360 Report"
    source_lower = html.lower()
    landscape = "report-landscape" in source_lower or "a4 landscape" in source_lower or "technical-financial" in source_lower or "technical report" in title.lower()
    blocks = _blocks(body)
    painter = _Painter(landscape=landscape, rtl=rtl, title=title)
    first = True
    for block in blocks:
        if block.kind == "page_break":
            if first:
                first = False
            else:
                painter.new_page()
        elif block.kind == "heading": painter.heading(str(block.payload), block.level)
        elif block.kind == "paragraph": painter.paragraph(str(block.payload))
        elif block.kind == "callout": painter.callout(str(block.payload), block.tone)
        elif block.kind == "cards": painter.cards(list(block.payload))
        elif block.kind == "table": painter.table(list(block.payload), compact=block.tone == "compact")
        elif block.kind == "rule": painter.rule()
    payload = painter.finish()
    if not payload.startswith(b"%PDF"):
        raise RuntimeError("The Python PDF renderer did not produce a valid PDF payload.")
    return payload
