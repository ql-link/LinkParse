from app.services.html_document import HtmlMarkdownRenderer, clean_semantic_html


def _render(html: str) -> tuple[str, HtmlMarkdownRenderer]:
    root, _, _ = clean_semantic_html(html)
    renderer = HtmlMarkdownRenderer()
    return renderer.render_children(root), renderer


def test_simple_table_uses_gfm_markdown_and_builds_table_ir():
    markdown, renderer = _render(
        """
        <table>
          <tr><td>字段</td><td>说明</td></tr>
          <tr><td>name</td><td>名称</td></tr>
        </table>
        """
    )

    assert "| 字段 | 说明 |" in markdown
    assert "<table>" not in markdown
    assert renderer.markdown_table_count == 1
    assert renderer.html_table_count == 0
    table_ir = renderer.table_irs[0]
    assert table_ir.row_count == 2
    assert table_ir.column_count == 2
    assert table_ir.complexity_reasons == []
    assert table_ir.to_dict()["cells"][3]["text"] == "名称"


def test_spanning_table_stays_as_safe_html_in_markdown():
    markdown, renderer = _render(
        """
        <table onclick="alert(1)">
          <thead>
            <tr><th rowspan="2">部门</th><th colspan="2">预算</th></tr>
            <tr><th>人力</th><th>设备</th></tr>
          </thead>
          <tbody>
            <tr><td>研发部</td><td>100 万</td><td>30 万</td></tr>
          </tbody>
        </table>
        """
    )

    assert 'LINKPARSE_TABLE_START id="table-001" format="html"' in markdown
    assert 'LINKPARSE_TABLE_END id="table-001"' in markdown
    assert 'reasons="colspan,multi_header,rowspan"' in markdown
    assert '<th rowspan="2">部门</th>' in markdown
    assert '<th colspan="2">预算</th>' in markdown
    assert "onclick" not in markdown
    assert "记录 1" not in markdown
    assert renderer.html_table_count == 1
    assert renderer.table_failure_count == 0
    assert renderer.table_irs[0].is_complex is True


def test_nested_and_image_table_stays_html_without_natural_language_rewrite():
    markdown, renderer = _render(
        """
        <table>
          <tr><td>类型</td><td>内容</td></tr>
          <tr>
            <td>组合</td>
            <td><p>说明</p><img src="word-assets/chart.png" alt="图表">
              <table><tr><td>子项</td></tr></table>
            </td>
          </tr>
        </table>
        """
    )

    assert "<table>" in markdown
    assert "word-assets/chart.png" in markdown
    assert "记录式表格" not in markdown
    assert renderer.image_count == 1
    reasons = renderer.table_irs[0].complexity_reasons
    assert "image_cell" in reasons
    assert "nested_table" in reasons
    assert "multi_block_cell" in reasons


def test_unbounded_cell_span_falls_back_to_source_html_without_expansion():
    markdown, renderer = _render(
        '<table><tr><td rowspan="999999999">内容</td></tr></table>'
    )

    assert 'LINKPARSE_TABLE_START id="table-001" format="html_fallback"' in markdown
    assert 'LINKPARSE_TABLE_END id="table-001"' in markdown
    assert 'rowspan="999999999"' in markdown
    assert renderer.table_failure_count == 1
