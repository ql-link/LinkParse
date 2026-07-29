import html
import re
from typing import Any


def outputs_from_pages(pages: list[dict[str, Any]], formats: set[str]) -> dict[str, Any]:
    text = "\n\n".join(page["text"] for page in pages if page["text"])
    outputs: dict[str, Any] = {}
    if "text" in formats:
        outputs["text"] = text
    if "json" in formats:
        outputs["json"] = {"pages": pages}
    if "markdown" in formats:
        outputs["markdown"] = "\n\n".join(
            f"## Page {page['page']}\n\n{page['text']}" for page in pages if page["text"]
        )
    if "html" in formats:
        outputs["html"] = "".join(
            f'<section data-page="{page["page"]}"><h2>Page {page["page"]}</h2>'
            f"<p>{html.escape(page['text']).replace(chr(10), '<br>')}</p></section>"
            for page in pages
            if page["text"]
        )
    return outputs


def fill_missing_outputs(outputs: dict[str, Any], formats: set[str]) -> dict[str, Any]:
    source = outputs.get("text") or outputs.get("markdown") or ""
    if "text" in formats and "text" not in outputs:
        outputs["text"] = re.sub(r"[#*_`>-]", "", source).strip()
    if "markdown" in formats and "markdown" not in outputs:
        outputs["markdown"] = outputs.get("text", source)
    if "html" in formats and "html" not in outputs:
        outputs["html"] = (
            f"<p>{html.escape(outputs.get('text', source)).replace(chr(10), '<br>')}</p>"
        )
    return outputs
