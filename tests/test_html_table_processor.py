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
    assert 'format="markdown" schema="gfm-table-v1"' in markdown
    assert 'LINKPARSE_TABLE_END id="table-001"' in markdown
    assert "<table>" not in markdown
    assert renderer.markdown_table_count == 1
    assert renderer.rag_text_table_count == 0
    table_ir = renderer.table_irs[0]
    assert table_ir.row_count == 2
    assert table_ir.column_count == 2
    assert table_ir.complexity_reasons == []
    assert table_ir.to_dict()["cells"][3]["text"] == "名称"


def test_spanning_table_uses_flattened_rag_text():
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

    assert (
        'LINKPARSE_TABLE_START id="table-001" format="rag_text" '
        'schema="table-rag-v1"' in markdown
    )
    assert 'LINKPARSE_TABLE_END id="table-001"' in markdown
    assert 'reasons="colspan,multi_header,rowspan"' in markdown
    assert "列：部门；预算/人力；预算/设备" in markdown
    assert "- 部门：研发部；预算/人力：100 万；预算/设备：30 万" in markdown
    assert "onclick" not in markdown
    assert "<table>" not in markdown
    assert "```json" not in markdown
    assert renderer.rag_text_table_count == 1
    assert renderer.table_failure_count == 0
    assert renderer.table_irs[0].is_complex is True


def test_nested_image_and_link_table_preserves_links_in_rag_text():
    markdown, renderer = _render(
        """
        <table>
          <tr><td>类型</td><td>内容</td></tr>
          <tr>
            <td>组合</td>
            <td><p><a href="https://example.com">说明</a></p>
              <img src="word-assets/chart.png" alt="图表">
              <table><tr><td>子项</td></tr></table>
            </td>
          </tr>
        </table>
        """
    )

    assert "![表格图片1](word-assets/chart.png)" in markdown
    assert "链接：[说明](https://example.com)" in markdown
    assert "嵌套表格：table-001-001" in markdown
    assert 'id="table-001-001" format="rag_text" schema="table-rag-v1"' in markdown
    assert (
        'parent_id="table-001" parent_row="2" parent_column="2"' in markdown
    )
    assert "内容：子项" in markdown
    assert "<table>" not in markdown
    assert renderer.image_count == 1
    reasons = renderer.table_irs[0].complexity_reasons
    assert "image_cell" in reasons
    assert "link_cell" in reasons
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


def test_rag_text_does_not_treat_cell_backticks_as_a_code_fence():
    markdown, _ = _render(
        "<table><tr><th>字段</th><th>值</th></tr>"
        '<tr><td rowspan="2">```代码```</td><td>第一行</td></tr>'
        "<tr><td>第二行</td></tr></table>"
    )

    assert "- 字段：```代码```；值：第一行" in markdown
    assert "- 字段：```代码```；值：第二行" in markdown
    assert "```json" not in markdown
    assert 'LINKPARSE_TABLE_END id="table-001" -->' in markdown


def test_rag_text_repeats_rowspan_value_for_each_logical_row():
    markdown, _ = _render(
        "<table><tr><th>区域</th><th>部门</th></tr>"
        '<tr><td rowspan="2">华东</td><td>研发</td></tr>'
        "<tr><td>产品</td></tr></table>"
    )

    assert "- 区域：华东；部门：研发" in markdown
    assert "- 区域：华东；部门：产品" in markdown


def test_rag_table_includes_heading_and_page_context():
    root, _, _ = clean_semantic_html(
        "<h1>年度报告</h1><h2>预算</h2>"
        '<table><tr><th rowspan="2">部门</th><th>金额</th></tr>'
        "<tr><td>100 万</td></tr></table>"
    )
    renderer = HtmlMarkdownRenderer(page_number=3)

    markdown = renderer.render_children(root)

    assert "章节：年度报告 / 预算" in markdown
    assert "页码：第 3 页" in markdown
