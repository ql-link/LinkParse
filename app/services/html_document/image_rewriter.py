import re
from urllib.parse import urlparse

from bs4 import Tag

from .models import ImageRewriteResult

SAFE_LINK_SCHEMES = {"", "http", "https", "mailto", "tel"}
SAFE_IMAGE_SCHEMES = {"", "http", "https"}


def safe_url(value: str, *, image: bool = False) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    scheme = urlparse(value).scheme.lower()
    allowed = SAFE_IMAGE_SCHEMES if image else SAFE_LINK_SCHEMES
    return value if scheme in allowed else ""


class HtmlImageRewriter:
    """Render sanitized image references without inventing storage URLs."""

    def rewrite_img(self, img: Tag) -> ImageRewriteResult:
        source = safe_url(str(img.get("src", "")), image=True)
        alt = self._clean_inline_text(str(img.get("alt", "")))
        if not source:
            return ImageRewriteResult(markdown=alt, source="")
        escaped_alt = alt.replace("[", "\\[").replace("]", "\\]")
        return ImageRewriteResult(markdown=f"![{escaped_alt}]({source})", source=source)

    @staticmethod
    def resolve_link(url: str) -> str:
        return safe_url(url)

    @staticmethod
    def _clean_inline_text(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()
