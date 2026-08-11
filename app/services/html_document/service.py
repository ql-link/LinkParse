from __future__ import annotations

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from app.core.errors import LinkParseError

from .image_rewriter import safe_url

NOISE_TAGS = {"script", "style", "noscript", "template", "iframe", "object", "embed"}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"rowspan", "colspan"},
    "th": {"rowspan", "colspan", "scope"},
    "ol": {"start"},
    "code": {"class"},
    "pre": {"class"},
}
def clean_semantic_html(html: str) -> tuple[Tag, str, int]:
    """Sanitize Mammoth HTML and return its body, serializable fragment and comment count."""
    if not html or not html.strip():
        raise LinkParseError("WORD_PARSE_FAILED", "Word parser produced no HTML", 422)

    soup = BeautifulSoup(html, "lxml")
    for node in soup.find_all(NOISE_TAGS):
        node.decompose()
    for node in soup.find_all(attrs={"hidden": True}):
        node.decompose()
    for node in soup.find_all(attrs={"aria-hidden": "true"}):
        node.decompose()

    comments = soup.find_all(string=lambda value: isinstance(value, Comment))
    for comment in comments:
        comment.extract()

    for node in soup.find_all(True):
        allowed = ALLOWED_ATTRIBUTES.get(node.name, set())
        node.attrs = {key: value for key, value in node.attrs.items() if key in allowed}
        if node.name == "a" and node.has_attr("href"):
            href = safe_url(str(node.get("href", "")))
            if href:
                node["href"] = href
            else:
                node.attrs.pop("href", None)
        elif node.name == "img":
            source = safe_url(str(node.get("src", "")), image=True)
            if not source:
                alt = str(node.get("alt", "")).strip()
                node.replace_with(NavigableString(alt)) if alt else node.decompose()
                continue
            node["src"] = source

    root = soup.body or soup
    fragment = "".join(str(child) for child in root.children).strip()
    if not fragment or not root.get_text(" ", strip=True) and not root.find("img"):
        raise LinkParseError("WORD_PARSE_FAILED", "Word document has no effective content", 422)
    return root, fragment, len(comments)
