import re
from html import escape

from bs4 import NavigableString, Tag

from .image_rewriter import HtmlImageRewriter
from .models import TableIR
from .table_processor import HtmlTableProcessor


class HtmlMarkdownRenderer:
    """Render sanitized semantic HTML to Markdown in DOM order."""

    CONTAINER_TAGS = {
        "html",
        "body",
        "main",
        "article",
        "section",
        "div",
        "header",
        "footer",
        "aside",
        "nav",
    }

    def __init__(
        self,
        *,
        table_id_start: int = 1,
        page_number: int | None = None,
        initial_heading_path: list[str] | None = None,
    ) -> None:
        self.image_rewriter = HtmlImageRewriter()
        self.table_processor = HtmlTableProcessor()
        self.table_count = 0
        self.markdown_table_count = 0
        self.rag_text_table_count = 0
        self.table_failure_count = 0
        self.table_irs: list[TableIR] = []
        self._next_table_id = table_id_start
        self.page_number = page_number
        self.heading_path = list(initial_heading_path or [])
        self.image_count = 0
        self.warnings: list[str] = []

    def render_children(self, node: Tag) -> str:
        return self._join_blocks([self.render_node(child) for child in node.children])

    def render_node(self, node: object) -> str:
        if isinstance(node, NavigableString):
            return self._clean_inline_text(str(node))
        if not isinstance(node, Tag):
            return ""

        name = node.name.lower()
        if name in self.CONTAINER_TAGS:
            return self.render_children(node)
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(name[1])
            text = self.render_inline_children(node)
            if text:
                self.heading_path = self.heading_path[: level - 1]
                self.heading_path.extend([""] * (level - 1 - len(self.heading_path)))
                self.heading_path.append(text)
            return f"{'#' * level} {text}" if text else ""
        if name == "p":
            return self.render_inline_children(node)
        if name in {"ul", "ol"}:
            return self.render_list(node, ordered=name == "ol")
        if name == "pre":
            return self.render_code_block(node)
        if name == "blockquote":
            return self.render_blockquote(node)
        if name == "br":
            return "\n"
        if name == "hr":
            return "---"
        if name == "img":
            result = self.image_rewriter.rewrite_img(node)
            self.image_count += 1
            if result.warning:
                self.warnings.append(result.warning)
            return result.markdown
        if name == "figure":
            body = self._join_blocks(
                [
                    self.render_node(child)
                    for child in node.children
                    if not (isinstance(child, Tag) and child.name.lower() == "figcaption")
                ]
            )
            caption = node.find("figcaption", recursive=False)
            if caption:
                caption_text = self.render_inline_children(caption)
                if caption_text:
                    body = self._join_blocks([body, f"图注：{caption_text}"])
            return body
        if name == "table":
            table_id = f"table-{self._next_table_id:03d}"
            self._next_table_id += 1
            result = self.table_processor.render(
                node,
                table_id=table_id,
                page_number=self.page_number,
                heading_path=[heading for heading in self.heading_path if heading],
            )
            self.table_count += 1
            if result.strategy == "markdown_table":
                self.markdown_table_count += 1
            elif result.strategy == "rag_text_table":
                self.rag_text_table_count += 1
            elif result.strategy == "html_fallback":
                self.table_failure_count += 1
            if result.table_ir is not None:
                self.table_irs.append(result.table_ir)
            self.image_count += result.image_count
            self.warnings.extend(result.warnings)
            if result.warning:
                self.warnings.append(result.warning)
            return result.markdown
        if name in {"script", "style", "noscript", "template"}:
            return ""
        if name == "code":
            text = self._clean_inline_text(node.get_text(" ", strip=True))
            return f"`{text}`" if text else ""
        return self.render_inline_children(node) or self.render_children(node)

    def render_inline_children(self, node: Tag) -> str:
        return self._clean_inline_text(
            "".join(self.render_inline(child) for child in node.children)
        )

    def render_inline(self, node: object) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""

        name = node.name.lower()
        if name == "br":
            return "\n"
        if name == "a":
            text = self.render_inline_children(node) or self._clean_inline_text(
                node.get_text(" ", strip=True)
            )
            href = self.image_rewriter.resolve_link(str(node.get("href", "")))
            return f"[{text}]({href})" if href else text
        if name == "img":
            result = self.image_rewriter.rewrite_img(node)
            self.image_count += 1
            if result.warning:
                self.warnings.append(result.warning)
            return result.markdown
        if name in {"strong", "b"}:
            text = self.render_inline_children(node)
            return f"**{text}**" if text else ""
        if name in {"em", "i"}:
            text = self.render_inline_children(node)
            return f"*{text}*" if text else ""
        if name in {"s", "strike", "del"}:
            text = self.render_inline_children(node)
            return f"~~{text}~~" if text else ""
        if name == "code":
            text = self._clean_inline_text(node.get_text(" ", strip=True))
            return f"`{text}`" if text else ""
        if name in {"script", "style", "noscript", "template"}:
            return ""
        return self.render_inline_children(node)

    def render_list(self, node: Tag, ordered: bool) -> str:
        lines: list[str] = []
        for index, li in enumerate(node.find_all("li", recursive=False), start=1):
            marker = f"{index}." if ordered else "-"
            content = self._join_blocks([self.render_node(child) for child in li.children])
            content_lines = content.splitlines() or [""]
            lines.append(f"{marker} {content_lines[0].strip()}")
            for continuation in content_lines[1:]:
                lines.append(f"  {continuation}".rstrip())
        return "\n".join(lines)

    def render_blockquote(self, node: Tag) -> str:
        blocks: list[str] = []
        quoted: list[str] = []

        def flush_quoted() -> None:
            text = self._join_blocks(quoted)
            quoted.clear()
            if text:
                blocks.append("\n".join(f"> {line}" if line else ">" for line in text.splitlines()))

        for child in node.children:
            if isinstance(child, Tag) and child.name.lower() == "pre":
                flush_quoted()
                blocks.append(self.render_code_block(child))
            else:
                quoted.append(self.render_node(child))
        flush_quoted()
        return self._join_blocks(blocks)

    def render_code_block(self, node: Tag) -> str:
        code = node.find("code")
        language = ""
        if code:
            for class_name in code.get("class", []):
                if class_name.startswith("language-"):
                    language = class_name.removeprefix("language-")
                    break
            text = code.get_text()
        else:
            text = node.get_text()
        return f"```{escape(language)}\n{text.rstrip()}\n```"

    @staticmethod
    def _join_blocks(parts: list[str]) -> str:
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

    @staticmethod
    def _clean_inline_text(text: str) -> str:
        text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
        text = re.sub(r" *\n *", "\n", text)
        return text.strip()
