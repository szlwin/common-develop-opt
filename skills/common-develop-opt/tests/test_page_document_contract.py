"""Regression coverage for canonical per-page documents and stable links."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from document_core import document_facts
from document_core import page_documents
from document_core import requirement_doc
from document_core import reference_resolution


def page_text(
    module: str,
    page_id: str,
    name: str = "登录",
    *,
    link: str = "",
) -> str:
    linked = f"\n[验收]({link})\n" if link else ""
    return (
        f"# {name}页面业务行为设计\n\n"
        "## 1. 设计信息\n\n"
        "| 项目 | 内容 |\n"
        "| --- | --- |\n"
        f"| 模块 | {module} |\n"
        f"| 主页面编号 | {page_id} |\n"
        f"| 页面中文名称 | {name} |\n"
        "| Design revision | PAGE-R01@test |\n"
        "| Base revision | PAGE-R00@init |\n"
        f"{linked}"
    )


class PageDocumentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project_doc"
        self.module_dir = self.root / "version" / "V_1.0" / "module" / "IAM"
        self.requirement_dir = self.root / "version" / "V_1.0" / "requirement" / "REQ-IAM-001"
        self.requirement_dir.mkdir(parents=True)
        self.page_dir = self.module_dir / "page-design"
        self.page_dir.mkdir(parents=True)

    def write_page(
        self,
        filename: str,
        page_id: str,
        *,
        module: str = "IAM",
        name: str = "登录",
        link: str = "",
    ) -> Path:
        path = self.page_dir / filename
        path.write_text(page_text(module, page_id, name, link=link), encoding="utf-8")
        return path

    def test_discovers_configurable_filename_and_owner_namespace(self) -> None:
        path = self.write_page("custom_login_design.md", "PAGE-IAM-LOGIN")

        documents = page_documents.discover_module_page_documents(self.module_dir, "IAM")

        self.assertEqual([path], [item.path for item in documents])
        self.assertEqual("PAGE-IAM-LOGIN", documents[0].stable_id)
        self.assertEqual("登录", documents[0].page_name)

        path.write_text(page_text("IAM", "PAGE-PLATFORM-LOGIN"), encoding="utf-8")
        with self.assertRaisesRegex(page_documents.PageDocumentError, "owner namespace"):
            page_documents.discover_module_page_documents(self.module_dir, "IAM")

    def test_duplicate_primary_page_id_fails_closed(self) -> None:
        self.write_page("login_design.md", "PAGE-IAM-LOGIN")
        self.write_page("login_alias_design.md", "PAGE-IAM-LOGIN")

        with self.assertRaisesRegex(page_documents.PageDocumentError, "duplicate primary PAGE ID"):
            page_documents.discover_module_page_documents(self.module_dir, "IAM")

    def test_legacy_page_is_readable_but_conflict_with_canonical_fails(self) -> None:
        legacy = self.module_dir / "IAM_page_design.md"
        legacy.write_text(
            "# IAM legacy page behavior\n\nPAGE-IAM-LOGIN PAGEACT-IAM-LOGIN\n",
            encoding="utf-8",
        )

        documents = page_documents.discover_module_page_documents(self.module_dir, "IAM")
        self.assertEqual(1, len(documents))
        self.assertTrue(documents[0].legacy)
        self.assertEqual("PAGE-IAM-BEHAVIOR", documents[0].stable_id)

        self.write_page("login_design.md", "PAGE-IAM-LOGIN")
        with self.assertRaisesRegex(page_documents.PageDocumentError, "legacy and canonical"):
            page_documents.discover_module_page_documents(self.module_dir, "IAM")

    def test_relative_ac_link_survives_chinese_title_change_and_stale_path_fails(self) -> None:
        requirement = self.requirement_dir / "requirement.md"
        requirement.write_text(
            '<a id="ac-req-iam-001-001"></a>\n\n'
            "### AC-REQ-IAM-001-001 用户可以登录\n",
            encoding="utf-8",
        )
        page = self.write_page(
            "login_design.md",
            "PAGE-IAM-LOGIN",
            link="../../../requirement/REQ-IAM-001/requirement.md#ac-req-iam-001-001",
        )
        documents = page_documents.discover_module_page_documents(self.module_dir, "IAM")
        self.assertEqual([], page_documents.validate_page_document_links(self.root, documents))

        requirement.write_text(
            '<a id="ac-req-iam-001-001"></a>\n\n'
            "### AC-REQ-IAM-001-001 完全不同的中文标题\n",
            encoding="utf-8",
        )
        self.assertEqual([], page_documents.validate_page_document_links(self.root, documents))

        requirement.rename(self.requirement_dir / "requirement_moved.md")
        errors = reference_resolution.validate_markdown_links(
            page,
            page.read_text(encoding="utf-8"),
            self.root,
        )
        self.assertTrue(any("target does not exist" in error for error in errors), errors)

    def test_module_description_gets_page_index_not_generic_extra_document(self) -> None:
        description = self.module_dir / "IAM_desc.md"
        description.write_text(
            document_facts.module_desc_template("IAM", "V_1.0"),
            encoding="utf-8",
        )
        self.write_page("login_design.md", "PAGE-IAM-LOGIN")

        updated, changed, extras = document_facts.synchronize_module_desc_source(
            description,
            "IAM",
            "V_1.0",
        )

        self.assertTrue(changed)
        self.assertEqual([], extras)
        self.assertIn("| PAGE-IAM-LOGIN | 登录 | [打开](page-design/login_design.md)", updated)
        self.assertNotIn("补充文档（自动）：[page-design/login_design.md]", updated)

    def test_module_description_sync_preserves_curated_page_metadata(self) -> None:
        description = self.module_dir / "IAM_desc.md"
        original = document_facts.module_desc_template("IAM", "V_1.0")
        empty_table = "\n".join(document_facts.render_table(document_facts.PAGE_HEADERS, []))
        curated = "\n".join(document_facts.render_table(document_facts.PAGE_HEADERS, [[
            "PAGE-IAM-LOGIN",
            "登录入口",
            "[旧入口](old-login.md)",
            "[原型](login.png)",
            "IAM.MOD001",
            "DEPRECATED",
        ]]))
        description.write_text(original.replace(empty_table, curated), encoding="utf-8")
        self.write_page("login_design.md", "PAGE-IAM-LOGIN", name="登录")

        updated, _, _ = document_facts.synchronize_module_desc_source(
            description,
            "IAM",
            "V_1.0",
        )

        self.assertIn(
            "| PAGE-IAM-LOGIN | 登录入口 | [打开](page-design/login_design.md) | "
            "[原型](login.png) | IAM.MOD001 | DEPRECATED |",
            updated,
        )

    def test_encoded_space_link_remains_valid_after_rebase(self) -> None:
        source = self.page_dir / "source_design.md"
        linked = self.page_dir / "a b.md"
        linked.write_text("# Space target\n", encoding="utf-8")
        source_text = "[space](a%20b.md)\n"
        source.write_text(source_text, encoding="utf-8")
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name

        self.assertEqual([], reference_resolution.validate_markdown_links(source, source_text, self.root))
        rebased = reference_resolution.rebase_markdown_links(source_text, source, target, self.root)

        self.assertIn("a%20b.md", rebased)
        self.assertNotIn("a b.md", rebased)
        self.assertEqual([], reference_resolution.validate_markdown_links(target, rebased, self.root))

    def test_reference_style_links_are_validated_and_rebased(self) -> None:
        requirement = self.requirement_dir / "requirement.md"
        requirement.write_text('<a id="ac-login"></a>\n', encoding="utf-8")
        source = self.page_dir / "source_design.md"
        source_text = "[验收][login]\n\n[login]: ../../../requirement/REQ-IAM-001/requirement.md#ac-login\n"
        source.write_text(source_text, encoding="utf-8")
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name

        self.assertEqual([], reference_resolution.validate_markdown_links(source, source_text, self.root))
        rebased = reference_resolution.rebase_markdown_links(source_text, source, target, self.root)
        self.assertIn(
            "[login]: ../../../../../version/V_1.0/requirement/REQ-IAM-001/requirement.md#ac-login",
            rebased,
        )
        self.assertEqual([], reference_resolution.validate_markdown_links(target, rebased, self.root))

        broken = source_text.replace("requirement.md", "missing.md")
        self.assertTrue(reference_resolution.validate_markdown_links(source, broken, self.root))
        absolute = source_text.replace("../../../requirement/REQ-IAM-001/requirement.md", str(requirement))
        errors = reference_resolution.validate_markdown_links(source, absolute, self.root)
        self.assertTrue(any("absolute Markdown link" in error for error in errors), errors)

    def test_multiline_reference_definitions_are_validated_and_rebased(self) -> None:
        requirement = self.requirement_dir / "requirement.md"
        requirement.write_text('<a id="ac-login"></a>\n', encoding="utf-8")
        source = self.page_dir / "source_design.md"
        source_text = "[验收][login]\n\n[login]:\n  ../../../requirement/REQ-IAM-001/requirement.md#ac-login\n"
        source.write_text(source_text, encoding="utf-8")
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name

        self.assertEqual([], reference_resolution.validate_markdown_links(source, source_text, self.root))
        rebased = reference_resolution.rebase_markdown_links(source_text, source, target, self.root)
        self.assertIn(
            "[login]:\n  ../../../../../version/V_1.0/requirement/REQ-IAM-001/requirement.md#ac-login",
            rebased,
        )
        self.assertEqual([], reference_resolution.validate_markdown_links(target, rebased, self.root))

        broken = source_text.replace("requirement.md", "missing.md")
        self.assertTrue(reference_resolution.validate_markdown_links(source, broken, self.root))
        absolute = source_text.replace("../../../requirement/REQ-IAM-001/requirement.md", str(requirement))
        errors = reference_resolution.validate_markdown_links(source, absolute, self.root)
        self.assertTrue(any("absolute Markdown link" in error for error in errors), errors)

    def test_multiline_reference_destination_accepts_deep_indent_and_tab(self) -> None:
        requirement = self.requirement_dir / "requirement.md"
        requirement.write_text('<a id="ac-login"></a>\n', encoding="utf-8")
        source = self.page_dir / "source_design.md"
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name

        for indent in ("    ", "      ", "\t"):
            with self.subTest(indent=repr(indent)):
                source_text = (
                    "[验收][login]\n\n"
                    f"[login]:\n{indent}../../../requirement/REQ-IAM-001/requirement.md#ac-login\n"
                )
                self.assertEqual(
                    [],
                    reference_resolution.validate_markdown_links(source, source_text, self.root),
                )
                rebased = reference_resolution.rebase_markdown_links(
                    source_text,
                    source,
                    target,
                    self.root,
                )
                self.assertIn(
                    f"[login]:\n{indent}../../../../../version/V_1.0/requirement/REQ-IAM-001/requirement.md#ac-login",
                    rebased,
                )
                self.assertEqual(
                    [],
                    reference_resolution.validate_markdown_links(target, rebased, self.root),
                )

                broken = source_text.replace("requirement.md", "missing.md")
                self.assertTrue(
                    reference_resolution.validate_markdown_links(source, broken, self.root)
                )
                absolute = source_text.replace("../../../requirement/REQ-IAM-001/requirement.md", str(requirement))
                errors = reference_resolution.validate_markdown_links(source, absolute, self.root)
                self.assertTrue(any("absolute Markdown link" in error for error in errors), errors)

    def test_links_inside_code_are_ignored_and_not_rewritten(self) -> None:
        source = self.page_dir / "source_design.md"
        source_text = (
            "`[inline](missing-inline.md)`\n\n"
            "```markdown\n[example](missing-fenced.md)\n```\n"
        )
        source.write_text(source_text, encoding="utf-8")
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name

        self.assertEqual([], reference_resolution.validate_markdown_links(source, source_text, self.root))
        self.assertEqual(
            source_text,
            reference_resolution.rebase_markdown_links(source_text, source, target, self.root),
        )

    def test_indented_code_links_are_ignored_but_indentation_cannot_interrupt_paragraph(self) -> None:
        source = self.page_dir / "source_design.md"
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name
        code = (
            "    [top-level](missing-top.md)\n\n"
            "- 示例\n\n"
            "      [list-code](missing-list.md)\n"
        )

        self.assertEqual([], reference_resolution.validate_markdown_links(source, code, self.root))
        self.assertEqual(code, reference_resolution.rebase_markdown_links(code, source, target, self.root))

        paragraph = "正文未结束\n    [real](missing-real.md)\n"
        self.assertTrue(reference_resolution.validate_markdown_links(source, paragraph, self.root))

    def test_only_valid_fence_closers_end_fenced_code(self) -> None:
        source = self.page_dir / "source_design.md"
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name
        source_text = (
            "```markdown\n"
            "```not-a-closing-fence\n"
            "[example](missing-fenced.md)\n"
            "```   \n\n"
            "````markdown\n"
            "```\n"
            "[short-marker](missing-short.md)\n"
            "````\n\n"
            "~~~markdown\n"
            "[tilde](missing-tilde.md)\n"
            "~~~\n"
        )

        self.assertEqual([], reference_resolution.validate_markdown_links(source, source_text, self.root))
        self.assertEqual(
            source_text,
            reference_resolution.rebase_markdown_links(source_text, source, target, self.root),
        )

    def test_inline_code_comment_literal_does_not_hide_real_link(self) -> None:
        source = self.page_dir / "source_design.md"
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name
        source_text = (
            "`<!--`\n\n"
            "[real](missing-real.md)\n\n"
            "<!-- `[comment-example](missing-comment.md)` -->\n"
        )

        errors = reference_resolution.validate_markdown_links(source, source_text, self.root)
        self.assertEqual(1, len(errors), errors)
        self.assertIn("missing-real.md", errors[0])
        rebased = reference_resolution.rebase_markdown_links(source_text, source, target, self.root)
        self.assertIn("[real](../", rebased)
        self.assertIn("`<!--`", rebased)
        self.assertIn("[comment-example](missing-comment.md)", rebased)

    def test_backtick_in_html_comment_cannot_pair_with_later_code_span(self) -> None:
        source = self.page_dir / "source_design.md"
        target = self.root / "docs" / "module" / "IAM" / "design" / "pages" / source.name
        source_text = "<!-- ` -->\n\n[real](missing-real.md)\n\n`literal`\n"

        errors = reference_resolution.validate_markdown_links(source, source_text, self.root)
        self.assertEqual(1, len(errors), errors)
        self.assertIn("missing-real.md", errors[0])
        rebased = reference_resolution.rebase_markdown_links(source_text, source, target, self.root)
        self.assertIn("<!-- ` -->", rebased)
        self.assertIn("[real](../", rebased)
        self.assertIn("`literal`", rebased)

    def test_non_markdown_fragment_returns_error_without_decoding_binary(self) -> None:
        source = self.page_dir / "source_design.md"
        image = self.page_dir / "image.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n\xff\x00")
        folder = self.page_dir / "folder"
        folder.mkdir()
        source_text = "![image](image.png#view)\n[folder](folder#view)\n"
        source.write_text(source_text, encoding="utf-8")

        errors = reference_resolution.validate_markdown_links(source, source_text, self.root)

        self.assertEqual(2, len(errors), errors)
        self.assertTrue(all("fragment target is not a Markdown file" in error for error in errors))

    def test_acceptance_anchor_generation_and_validation_are_title_independent(self) -> None:
        acceptance_id = "AC-REQ-IAM-001-001"
        first = '<a id="ac-req-iam-001-001"></a>\n\n### AC-REQ-IAM-001-001 登录成功\n'
        renamed = first.replace("登录成功", "任意中文标题")

        generated = requirement_doc.acceptance_block(
            requirement_doc.Feature("REQ-IAM-001-F01", "登录", "IAM", "", "用户登录"),
            1,
            "REQ-IAM-001",
        )

        self.assertEqual("ac-req-iam-001-001", requirement_doc.acceptance_anchor(acceptance_id))
        self.assertTrue(generated.startswith('<a id="ac-req-iam-001-001"></a>\n\n### AC-REQ-IAM-001-001 登录'))
        self.assertEqual([], requirement_doc._validate_acceptance_anchors(first, [acceptance_id]))
        self.assertEqual([], requirement_doc._validate_acceptance_anchors(renamed, [acceptance_id]))

        missing = "### AC-REQ-IAM-001-001 登录成功\n"
        mismatch = first.replace("ac-req-iam-001-001", "ac-req-iam-001-999")
        duplicate = '<a id="ac-req-iam-001-001"></a>\n' + first
        self.assertTrue(any(
            "missing explicit anchor" in item
            for item in requirement_doc._validate_acceptance_anchors(missing, [acceptance_id])
        ))
        self.assertTrue(any(
            "does not match" in item
            for item in requirement_doc._validate_acceptance_anchors(mismatch, [acceptance_id])
        ))
        self.assertTrue(any(
            "duplicate acceptance anchor" in item
            for item in requirement_doc._validate_acceptance_anchors(duplicate, [acceptance_id])
        ))

    def test_requirement_analysis_validator_enforces_explicit_ac_anchors(self) -> None:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "structured-docs"
            / "requirement.multi-feature.example.md"
        ).read_text(encoding="utf-8")
        anchored = re.sub(
            r"(?m)^(### (AC-[A-Z0-9_-]+) .*)$",
            lambda match: f'<a id="{match.group(2).lower()}"></a>\n\n{match.group(1)}',
            fixture,
        )
        path = self.root / "requirement.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(anchored, encoding="utf-8")

        self.assertTrue(requirement_doc.validate_requirement(path, "analysis").valid)

        path.write_text(anchored.replace('<a id="ac-demo1001-002"></a>\n\n', ""), encoding="utf-8")
        result = requirement_doc.validate_requirement(path, "analysis")
        self.assertFalse(result.valid)
        self.assertTrue(any("missing explicit anchor" in error for error in result.errors))

    def test_page_template_keeps_trace_id_plain_and_acceptance_linked(self) -> None:
        template = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "documents"
            / "structured-docs"
            / "page_design.template.md"
        ).read_text(encoding="utf-8")

        trace_row = next(line for line in template.splitlines() if line.startswith("| {TRACE_ID} |"))
        self.assertNotIn("[{TRACE_ID}]", trace_row)
        self.assertIn("[{ACCEPTANCE_ID}]", trace_row)

    def test_design_template_points_to_canonical_page_layout(self) -> None:
        template = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "documents"
            / "structured-docs"
            / "design.template.md"
        ).read_text(encoding="utf-8")

        self.assertIn("page-design/{PAGE_DOCUMENT_STEM}_design.md", template)
        self.assertNotIn("`*_page_design.md`", template)


if __name__ == "__main__":
    unittest.main()
