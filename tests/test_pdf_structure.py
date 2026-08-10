from app.services.pdf_structure import analyze_pdf_markdown, image_page_map


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
