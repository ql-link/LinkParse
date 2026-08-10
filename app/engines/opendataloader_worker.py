from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--formats", required=True)
    parser.add_argument("--table-method", choices=("default", "cluster"), default="default")
    parser.add_argument("--markdown-with-html", action="store_true")
    parser.add_argument("--include-images", action="store_true")
    args = parser.parse_args()
    try:
        import opendataloader_pdf

        opendataloader_pdf.convert(
            input_path=[args.input],
            output_dir=args.output_dir,
            format=args.formats,
            table_method=args.table_method,
            markdown_with_html=args.markdown_with_html,
            markdown_page_separator="<!-- ODL_PAGE:%page-number% -->",
            html_page_separator="<!-- ODL_PAGE:%page-number% -->",
            image_output="external" if args.include_images else "off",
            image_format="png",
            image_dir=args.image_dir if args.include_images else None,
            quiet=True,
        )
    except Exception as exc:
        payload = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
