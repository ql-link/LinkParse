import pytest

from app.services import pdf_structure
from app.services.pdf_structure import (
    analyze_pdf_markdown,
    build_pdf_layout,
    image_page_map,
    markdown_from_layout,
)


def _blank_pdf(path, page_count=1):
    import fitz

    document = fitz.open()
    for _ in range(page_count):
        document.new_page(width=200, height=220)
    document.save(path)
    document.close()


def _ocr_block(text, x0, y0, x1, y1, confidence=0.99):
    return {
        "type": "text",
        "text": text,
        "bbox": [x0, y0, x1, y1],
        "confidence": confidence,
    }


def test_analyze_pdf_markdown_keeps_page_provenance_and_tables():
    markdown = """<!-- ODL_PAGE:1 -->

| 项目 | 数值 |
| --- | ---: |
| 碳排放 | 42 |

<!-- ODL_PAGE:2 -->

<table><tr><th>能源</th><th>用量</th></tr><tr><td>电</td><td>10</td></tr></table>
"""

    report = analyze_pdf_markdown(markdown, 2)

    assert report["page_provenance_complete"] is True
    assert report["page_markers"] == [1, 2]
    assert report["table_count"] == 2
    assert report["tables"][0]["source_page"] == 1
    assert report["tables"][0]["rows"][1] == ["碳排放", "42"]
    assert report["tables"][1]["source_page"] == 2
    assert report["tables"][1]["header"] == ["能源", "用量"]


def test_analyze_pdf_markdown_warns_when_page_marker_is_missing():
    report = analyze_pdf_markdown("<!-- ODL_PAGE:2 -->\ntext", 2)

    assert report["page_provenance_complete"] is False
    assert report["warnings"] == ["PAGE_MARKERS_INCOMPLETE"]


def test_image_page_map_uses_markers_instead_of_filename_guesses():
    markdown = """<!-- ODL_PAGE:3 -->
![figure](images/page-99.png)
<!-- ODL_PAGE:4 -->
![other](<images/chart one.png>)
"""

    assert image_page_map(markdown) == {
        "images/page-99.png": 3,
        "images/chart one.png": 4,
    }


def test_pdf_layout_contract_preserves_rows_lists_and_continuations(tmp_path):
    path = tmp_path / "layout.pdf"
    _blank_pdf(path)
    blocks = [
        _ocr_block("Education", 10, 10, 70, 18),
        # The right block starts one pixel higher; row-aware ordering must
        # still emit the left block first.
        _ocr_block("School", 10, 31, 85, 36),
        _ocr_block("2024", 130, 30, 190, 35),
        _ocr_block("Skills", 10, 50, 65, 58),
        _ocr_block("Python", 10, 65, 80, 70),
        _ocr_block("Internship", 10, 85, 80, 93),
        _ocr_block("1.Architecture", 10, 100, 100, 105),
        _ocr_block("continued", 10, 106, 100, 111),
        _ocr_block("Open Source", 10, 130, 90, 138),
        _ocr_block("Project Atlas", 10, 145, 75, 150),
    ]

    layout = build_pdf_layout(
        path,
        markdown="",
        page_count=1,
        ocr_pages={1: {"page": 1, "width": 200, "height": 220, "blocks": blocks}},
    )

    assert layout["schema_version"] == 1
    assert layout["quality"]["status"] == "passed"
    assert layout["quality"]["source_line_count"] == 10
    assert layout["quality"]["output_block_count"] == 10
    assert layout["quality"]["heading_count"] == 4
    assert layout["quality"]["warnings"] == [
        "ocr_used",
        "role_inferred",
        "heading_inferred",
        "visual_role_inferred",
        "row_inferred",
        "continuation_inferred",
    ]

    output = layout["blocks"]
    assert [block["text"] for block in output if block["role"] == "heading"] == [
        "Education",
        "Skills",
        "Internship",
        "Open Source",
    ]
    assert output[1]["row_id"] == output[2]["row_id"]
    assert output[1]["join_with"] == output[2]["join_with"] == ""
    assert output[7]["continuation_of"] == output[6]["block_id"]
    assert output[7]["join_with"] == " "
    assert output[6]["role"] == output[7]["role"] == "ordered_list_item"
    assert output[6]["role_source"] == output[7]["role_source"] == "ocr"
    assert all(0 <= coordinate <= 1 for block in output for coordinate in block["bbox"])
    assert markdown_from_layout(output) == (
        "# Education\n\nSchool ｜ 2024\n\n# Skills\n\nPython\n\n"
        "# Internship\n\n1. Architecture continued\n\n# Open Source\n\nProject Atlas"
    )


def test_pdf_layout_degrades_for_independent_two_column_reading_order(tmp_path):
    path = tmp_path / "two-columns.pdf"
    _blank_pdf(path)
    left = [
        _ocr_block("Left one", 10, 20, 85, 26),
        _ocr_block("Left two", 10, 40, 85, 46),
        _ocr_block("Left three", 10, 60, 85, 66),
    ]
    right = [
        _ocr_block("Right one", 120, 24, 190, 30),
        _ocr_block("Right two", 120, 44, 190, 50),
        _ocr_block("Right three", 120, 64, 190, 70),
    ]
    layout = build_pdf_layout(
        path,
        markdown="",
        page_count=1,
        ocr_pages={1: {"page": 1, "width": 200, "height": 220, "blocks": left + right}},
    )

    assert layout["quality"]["status"] == "degraded"
    assert layout["quality"]["source_line_count"] == 6
    assert layout["quality"]["output_block_count"] == 6
    assert "layout_ambiguous" in layout["quality"]["warnings"]


def test_pdf_layout_degrades_for_aligned_balanced_columns_instead_of_rows(tmp_path):
    path = tmp_path / "aligned-columns.pdf"
    _blank_pdf(path)
    blocks = [_ocr_block("Resume", 10, 5, 80, 15)]
    blocks.extend(
        _ocr_block(f"Left {index}", 10, y, 80, y + 6)
        for index, y in enumerate((30, 55, 80), start=1)
    )
    blocks.extend(
        _ocr_block(f"Right {index}", 120, y, 190, y + 6)
        for index, y in enumerate((30, 55, 80), start=1)
    )

    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Resume",
        page_count=1,
        ocr_pages={1: {"width": 200, "height": 220, "blocks": blocks}},
    )

    assert layout["quality"]["status"] == "degraded"
    assert "layout_ambiguous" in layout["quality"]["warnings"]


def test_pdf_layout_degrades_for_compact_columns_and_does_not_cross_join(tmp_path):
    path = tmp_path / "compact-columns.pdf"
    _blank_pdf(path)
    blocks = [
        _ocr_block("Resume", 10, 5, 80, 15),
        _ocr_block("Left one", 10, 30, 80, 36),
        _ocr_block("Left two", 10, 55, 80, 61),
        _ocr_block("Right one", 120, 34, 190, 40),
        _ocr_block("Right two", 120, 59, 190, 65),
    ]

    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Resume",
        page_count=1,
        ocr_pages={1: {"width": 200, "height": 220, "blocks": blocks}},
    )

    assert layout["quality"]["status"] == "degraded"
    assert "layout_ambiguous" in layout["quality"]["warnings"]
    assert all(block["continuation_of"] is None for block in layout["blocks"])


def test_pdf_layout_keeps_repeated_narrow_right_metadata_rows(tmp_path):
    path = tmp_path / "metadata-rows.pdf"
    _blank_pdf(path)
    blocks = [_ocr_block("Experience", 10, 5, 80, 15)]
    for index, y in enumerate((30, 55, 80), start=1):
        blocks.append(_ocr_block(f"Company {index}", 10, y, 110, y + 6))
        blocks.append(_ocr_block(f"20{index}0", 165, y, 190, y + 6))

    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Experience",
        page_count=1,
        ocr_pages={1: {"width": 200, "height": 220, "blocks": blocks}},
    )

    assert layout["quality"]["status"] == "passed"
    assert "layout_ambiguous" not in layout["quality"]["warnings"]
    assert layout["quality"]["row_group_count"] == 3


def test_pdf_layout_source_order_is_page_then_bbox_not_global_y(tmp_path):
    path = tmp_path / "two-pages.pdf"
    _blank_pdf(path, page_count=2)
    layout = build_pdf_layout(
        path,
        markdown="",
        page_count=2,
        ocr_pages={
            1: {
                "page": 1,
                "width": 200,
                "height": 220,
                "blocks": [_ocr_block("Page one", 10, 180, 100, 186)],
            },
            2: {
                "page": 2,
                "width": 200,
                "height": 220,
                "blocks": [_ocr_block("Page two", 10, 10, 100, 16)],
            },
        },
    )

    assert [block["source_page"] for block in layout["blocks"]] == [1, 2]
    assert [block["source_order"] for block in layout["blocks"]] == [0, 1]
    assert [block["text"] for block in layout["blocks"]] == ["Page one", "Page two"]


def test_pdf_layout_recovers_bbox_entirely_outside_page_without_invalid_dto(tmp_path):
    from app.schemas.models import PdfLayout

    path = tmp_path / "outside-bbox.pdf"
    _blank_pdf(path)
    layout = build_pdf_layout(
        path,
        markdown="# Heading",
        page_count=1,
        ocr_pages={
            1: {
                "page": 1,
                "width": 200,
                "height": 220,
                "blocks": [_ocr_block("Heading", -20, 10, -5, 20)],
            }
        },
    )

    assert layout["quality"]["status"] == "degraded"
    assert layout["blocks"][0]["bbox"] == [0.0, 0.0, 1.0, 1.0]
    assert "bbox_recovered" in layout["quality"]["warnings"]
    PdfLayout.model_validate(layout)


def test_pdf_layout_degrades_before_quadratic_inference_when_source_limit_is_exceeded(
    tmp_path, monkeypatch
):
    path = tmp_path / "too-many-blocks.pdf"
    _blank_pdf(path)
    monkeypatch.setattr(pdf_structure, "MAX_LAYOUT_SOURCE_BLOCKS", 2)
    monkeypatch.setattr(
        pdf_structure,
        "_infer_heading_candidates",
        lambda _lines: pytest.fail("quadratic inference must not run"),
    )

    layout = pdf_structure.build_pdf_layout(
        path,
        markdown="",
        page_count=1,
        ocr_pages={
            1: {
                "page": 1,
                "width": 200,
                "height": 220,
                "blocks": [
                    _ocr_block("one", 10, 10, 40, 18),
                    _ocr_block("two", 10, 30, 40, 38),
                    _ocr_block("three", 10, 50, 50, 58),
                ],
            }
        },
    )

    assert layout == {
        "schema_version": 1,
        "blocks": [],
        "quality": {
            "status": "degraded",
            "source_line_count": 3,
            "output_block_count": 0,
            "heading_count": 0,
            "row_group_count": 0,
            "warnings": ["layout_source_limit_exceeded"],
        },
    }


def test_pdf_layout_uses_visible_list_marker_over_incorrect_odl_role(tmp_path):
    path = tmp_path / "visible-list-role.pdf"
    _blank_pdf(path)
    blocks = [
        _ocr_block("Experience", 10, 5, 90, 15),
        _ocr_block("1.First", 10, 30, 150, 38),
        _ocr_block("2、Second", 10, 45, 150, 53),
        _ocr_block("•Third", 10, 60, 150, 68),
    ]
    odl_json = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {"text": "Experience", "type": "heading1"},
                    {"text": "1.First", "type": "bullet_list_item"},
                    {"text": "2、Second", "type": "list_item"},
                    {"text": "•Third", "type": "list_item"},
                ],
            }
        ]
    }

    layout = build_pdf_layout(
        path,
        markdown="",
        page_count=1,
        odl_json=odl_json,
        ocr_pages={1: {"width": 200, "height": 220, "blocks": blocks}},
    )

    assert [block["role"] for block in layout["blocks"]] == [
        "heading",
        "ordered_list_item",
        "ordered_list_item",
        "bullet_list_item",
    ]
    assert markdown_from_layout(layout["blocks"]) == (
        "# Experience\n\n1. First\n2. Second\n\n- Third"
    )


def test_pdf_layout_does_not_treat_page_number_or_generic_list_as_ordered(tmp_path):
    path = tmp_path / "page-number.pdf"
    _blank_pdf(path)
    blocks = [
        _ocr_block("Resume", 10, 5, 80, 15),
        _ocr_block("Plain item", 10, 40, 100, 48),
        _ocr_block("1", 95, 200, 105, 210),
    ]
    odl_json = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {"text": "Resume", "type": "heading1"},
                    {"text": "Plain item", "type": "list_item"},
                    {"text": "1", "type": "page_number"},
                ],
            }
        ]
    }

    layout = build_pdf_layout(
        path,
        markdown="",
        page_count=1,
        odl_json=odl_json,
        ocr_pages={1: {"width": 200, "height": 220, "blocks": blocks}},
    )

    assert [block["role"] for block in layout["blocks"]] == [
        "heading",
        "paragraph",
        "paragraph",
    ]
    assert "1. 1" not in markdown_from_layout(layout["blocks"])


def test_pdf_layout_recognizes_bullets_without_spaces_and_html_entities(tmp_path):
    path = tmp_path / "compact-bullets.pdf"
    _blank_pdf(path)
    blocks = [
        _ocr_block("Experience", 10, 5, 90, 15),
        _ocr_block("•First", 10, 30, 100, 38),
        _ocr_block("-Second", 10, 45, 100, 53),
        _ocr_block("+&#x20;Third", 10, 60, 120, 68),
    ]

    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Experience",
        page_count=1,
        ocr_pages={1: {"width": 200, "height": 220, "blocks": blocks}},
    )

    assert [block["role"] for block in layout["blocks"][1:]] == [
        "bullet_list_item",
        "bullet_list_item",
        "bullet_list_item",
    ]
    assert markdown_from_layout(layout["blocks"]) == ("# Experience\n\n- First\n- Second\n- Third")


def test_pdf_layout_uses_same_size_bold_text_as_boundary_heading(tmp_path):
    import fitz

    path = tmp_path / "bold-headings.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=250)
    page.insert_text((20, 30), "Education", fontname="hebo", fontsize=10)
    page.insert_text((20, 44), "University 2020 - 2024", fontname="helv", fontsize=10)
    page.insert_text((20, 58), "Computer Science", fontname="helv", fontsize=10)
    page.insert_text((20, 80), "Experience", fontname="hebo", fontsize=10)
    page.insert_text((20, 94), "Company", fontname="helv", fontsize=10)
    document.save(path)
    document.close()

    layout = build_pdf_layout(path, markdown="", page_count=1)

    assert layout["quality"]["status"] == "passed"
    assert [block["text"] for block in layout["blocks"] if block["role"] == "heading"] == [
        "Education",
        "Experience",
    ]
    assert markdown_from_layout(layout["blocks"]) == (
        "# Education\n\nUniversity 2020 - 2024 Computer Science\n\n# Experience\n\nCompany"
    )


def test_pdf_layout_links_strong_cross_page_list_continuation(tmp_path):
    path = tmp_path / "cross-page.pdf"
    _blank_pdf(path, page_count=2)
    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Experience\n<!-- ODL_PAGE:2 -->",
        page_count=2,
        ocr_pages={
            1: {
                "width": 200,
                "height": 220,
                "blocks": [
                    _ocr_block("Experience", 10, 10, 100, 20),
                    _ocr_block("1. Implemented parser and", 10, 198, 190, 212),
                ],
            },
            2: {
                "width": 200,
                "height": 220,
                "blocks": [_ocr_block("preserved layout", 10, 8, 180, 20)],
            },
        },
    )

    continuation = layout["blocks"][2]
    assert layout["quality"]["status"] == "passed"
    assert continuation["continuation_of"] == layout["blocks"][1]["block_id"]
    assert continuation["role"] == "ordered_list_item"
    assert markdown_from_layout(layout["blocks"]) == (
        "# Experience\n\n1. Implemented parser and preserved layout"
    )


def test_pdf_layout_rejects_cross_page_continuation_from_visual_row(tmp_path):
    path = tmp_path / "cross-page-row.pdf"
    _blank_pdf(path, page_count=2)
    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Experience\n<!-- ODL_PAGE:2 -->",
        page_count=2,
        ocr_pages={
            1: {
                "width": 200,
                "height": 220,
                "blocks": [
                    _ocr_block("Experience", 10, 10, 100, 20),
                    _ocr_block("1. Left item", 10, 198, 90, 212),
                    _ocr_block("2. Right item", 110, 198, 190, 212),
                ],
            },
            2: {
                "width": 200,
                "height": 220,
                "blocks": [_ocr_block("continued text", 110, 8, 190, 20)],
            },
        },
    )

    assert layout["quality"]["status"] == "degraded"
    assert "layout_ambiguous" in layout["quality"]["warnings"]
    assert layout["blocks"][-1]["continuation_of"] is None


def test_pdf_layout_rejects_cross_page_continuation_into_visual_row(tmp_path):
    path = tmp_path / "cross-page-target-row.pdf"
    _blank_pdf(path, page_count=2)
    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Experience\n<!-- ODL_PAGE:2 -->",
        page_count=2,
        ocr_pages={
            1: {
                "width": 200,
                "height": 220,
                "blocks": [
                    _ocr_block("Experience", 10, 10, 100, 20),
                    _ocr_block("1. Independent item", 10, 198, 100, 212),
                ],
            },
            2: {
                "width": 200,
                "height": 220,
                "blocks": [
                    _ocr_block("continued text", 10, 8, 90, 20),
                    _ocr_block("right metadata", 110, 8, 190, 20),
                ],
            },
        },
    )

    assert layout["quality"]["status"] == "degraded"
    assert "layout_ambiguous" in layout["quality"]["warnings"]
    continued = next(block for block in layout["blocks"] if block["text"] == "continued text")
    assert continued["row_id"] is not None
    assert continued["continuation_of"] is None


def test_pdf_layout_normalizes_heterogeneous_row_roles_without_degrading(tmp_path):
    path = tmp_path / "mixed-row.pdf"
    _blank_pdf(path)
    blocks = [
        _ocr_block("Resume", 10, 5, 80, 15),
        _ocr_block("Company", 10, 35, 100, 45),
        _ocr_block("2024", 150, 35, 190, 45),
    ]
    odl_json = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {"text": "Resume", "type": "heading1"},
                    {"text": "Company", "type": "heading1"},
                    {"text": "2024", "type": "paragraph"},
                ],
            }
        ]
    }

    layout = build_pdf_layout(
        path,
        markdown="",
        page_count=1,
        odl_json=odl_json,
        ocr_pages={1: {"width": 200, "height": 220, "blocks": blocks}},
    )

    assert layout["quality"]["status"] == "passed"
    assert "row_role_recovered" in layout["quality"]["warnings"]


def test_pdf_layout_rechecks_rows_after_wire_bbox_rounding(tmp_path):
    path = tmp_path / "wire-row-rounding.pdf"
    _blank_pdf(path)
    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Experience",
        page_count=1,
        ocr_pages={
            1: {
                "width": 1,
                "height": 1,
                "blocks": [
                    _ocr_block("Experience", 0.05, 0.05, 0.3, 0.1),
                    _ocr_block(
                        "Left",
                        0.1560401863833365,
                        0.2,
                        0.6475286179987133,
                        0.22,
                    ),
                    _ocr_block(
                        "Right",
                        0.61688658853512,
                        0.2,
                        0.7871200918354199,
                        0.22,
                    ),
                ],
            }
        },
    )

    assert layout["quality"]["status"] == "degraded"
    assert layout["quality"]["row_group_count"] == 0
    assert "layout_ambiguous" in layout["quality"]["warnings"]
    assert "row_inferred" not in layout["quality"]["warnings"]
    assert all(block["row_id"] is None for block in layout["blocks"])


def test_pdf_layout_rechecks_continuations_after_wire_bbox_rounding(tmp_path):
    path = tmp_path / "wire-continuation-rounding.pdf"
    _blank_pdf(path)
    layout = build_pdf_layout(
        path,
        markdown="<!-- ODL_PAGE:1 -->\n# Experience",
        page_count=1,
        ocr_pages={
            1: {
                "width": 1,
                "height": 1,
                "blocks": [
                    _ocr_block("Experience", 0.1, 0.05, 0.5, 0.0789411668),
                    _ocr_block(
                        "Body text",
                        0.1,
                        0.30174271657,
                        0.8,
                        0.37707943382,
                    ),
                    _ocr_block(
                        "continued text",
                        0.1,
                        0.41851462781,
                        0.8,
                        0.49666556325,
                    ),
                ],
            }
        },
    )

    assert layout["quality"]["status"] == "degraded"
    assert "layout_ambiguous" in layout["quality"]["warnings"]
    continued = next(block for block in layout["blocks"] if block["text"] == "continued text")
    assert continued["continuation_of"] is None
    assert continued["join_with"] == ""
    assert [block["role"] for block in layout["blocks"][1:]] == [
        "paragraph",
        "paragraph",
    ]


def test_pdf_layout_degrades_transitive_row_that_violates_adjacent_geometry(tmp_path):
    path = tmp_path / "transitive-row.pdf"
    _blank_pdf(path)
    layout = build_pdf_layout(
        path,
        markdown="# Resume",
        page_count=1,
        ocr_pages={
            1: {
                "width": 1,
                "height": 1,
                "blocks": [
                    _ocr_block("Resume", 0.05, 0.02, 0.2, 0.04),
                    _ocr_block("Left", 0.211461, 0.2, 0.306188, 0.22),
                    _ocr_block("Middle", 0.287477, 0.2, 0.486704, 0.22),
                    _ocr_block("Right", 0.712435, 0.2, 0.929006, 0.22),
                ],
            }
        },
    )

    assert layout["quality"]["status"] == "degraded"
    assert "layout_ambiguous" in layout["quality"]["warnings"]
    assert all(block["row_id"] is None for block in layout["blocks"])


def test_pdf_layout_degrades_when_semantic_or_text_budget_is_exceeded(tmp_path, monkeypatch):
    path = tmp_path / "input-limit.pdf"
    _blank_pdf(path)
    monkeypatch.setattr(pdf_structure, "MAX_LAYOUT_ROLE_RECORDS", 1)
    odl_json = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {"text": "one", "type": "paragraph"},
                    {"text": "two", "type": "paragraph"},
                ],
            }
        ]
    }

    layout = build_pdf_layout(path, markdown="", page_count=1, odl_json=odl_json)

    assert layout["quality"]["status"] == "degraded"
    assert layout["blocks"] == []
    assert layout["quality"]["warnings"] == ["layout_input_limit_exceeded"]

    monkeypatch.setattr(pdf_structure, "MAX_LAYOUT_LINE_CHARS", 5)
    source_layout = build_pdf_layout(
        path,
        markdown="",
        page_count=1,
        ocr_pages={
            1: {
                "width": 200,
                "height": 220,
                "blocks": [_ocr_block("too long", 10, 10, 100, 20)],
            }
        },
    )
    assert source_layout["blocks"] == []
    assert source_layout["quality"]["source_line_count"] == 1
    assert source_layout["quality"]["warnings"] == ["layout_input_limit_exceeded"]
