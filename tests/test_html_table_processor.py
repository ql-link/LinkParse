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


def test_spanning_table_uses_compact_rag_text():
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
        'schema="table-rag-v2"' in markdown
    )
    assert 'LINKPARSE_TABLE_END id="table-001"' in markdown
    assert 'reasons="colspan,multi_header,rowspan"' in markdown
    assert "表头：\n- 部门\n- 预算：人力、设备" in markdown
    assert "数据：\n- 行1：部门：研发部 | 人力：100 万 | 设备：30 万" in markdown
    assert "onclick" not in markdown
    assert "<table>" not in markdown
    assert "```json" not in markdown
    assert renderer.rag_text_table_count == 1
    assert renderer.table_failure_count == 0
    assert renderer.table_irs[0].is_complex is True
    preview = renderer.table_previews[0]
    assert preview["schema"] == "table-ir-preview-v1"
    assert preview["row_count"] == 3
    assert preview["column_count"] == 3
    assert preview["cells"][0] == {
        "row": 0,
        "column": 0,
        "row_span": 2,
        "column_span": 1,
        "is_header": True,
        "markdown": "部门",
    }
    assert preview["cells"][1]["column_span"] == 2


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
    assert 'id="table-001-001" format="rag_text" schema="table-rag-v2"' in markdown
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
    assert [item["id"] for item in renderer.table_previews] == [
        "table-001",
        "table-001-001",
    ]
    parent_value = renderer.table_previews[0]["cells"][3]["markdown"]
    assert "[说明](https://example.com)" in parent_value
    assert "![表格图片1](word-assets/chart.png)" in parent_value


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

    assert "- 行1：字段：```代码``` | 值：第一行" in markdown
    assert "- 行2：字段：```代码``` | 值：第二行" in markdown
    assert "```json" not in markdown
    assert 'LINKPARSE_TABLE_END id="table-001" -->' in markdown


def test_rag_text_escapes_a_literal_field_delimiter():
    markdown, _ = _render(
        "<table><tr><th>字段</th><th>值</th></tr>"
        '<tr><td rowspan="2">A | B</td><td>第一行</td></tr>'
        "<tr><td>第二行</td></tr></table>"
    )

    assert "字段：A \\| B | 值：第一行" in markdown


def test_rag_text_repeats_rowspan_value_for_each_logical_row():
    markdown, _ = _render(
        "<table><tr><th>区域</th><th>部门</th></tr>"
        '<tr><td rowspan="2">华东</td><td>研发</td></tr>'
        "<tr><td>产品</td></tr></table>"
    )

    assert "- 行1：区域：华东 | 部门：研发" in markdown
    assert "- 行2：区域：华东 | 部门：产品" in markdown


def test_full_width_header_is_used_as_title_instead_of_repeated_field_prefix():
    markdown, renderer = _render(
        "<table><thead>"
        '<tr><th colspan="3">项目评估矩阵</th></tr>'
        '<tr><th>序号</th><th colspan="2">结果与说明</th></tr>'
        '<tr><th>编号</th><th>当前状态</th><th>目标</th></tr>'
        "</thead><tbody>"
        "<tr><td>1</td><td>已完成</td><td>保持</td></tr>"
        "</tbody></table>"
    )

    assert "表格：项目评估矩阵" in markdown
    assert "- 序号：编号" in markdown
    assert "- 结果与说明：当前状态、目标" in markdown
    assert "- 行1：编号：1 | 当前状态：已完成 | 目标：保持" in markdown
    assert "项目评估矩阵/" not in markdown
    preview = renderer.table_previews[0]
    assert preview["cells"][0]["column_span"] == 3
    assert preview["cells"][1]["column_span"] == 1
    assert preview["cells"][2]["column_span"] == 2


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
