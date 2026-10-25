"""Selection-return contracts are checked at portal/archive and Case boundaries."""
import tempfile
import unittest
from pathlib import Path

from document_core import page_documents, page_relations
from document_core._merge_docs import model
from runtime import case_doc


def table(marker, columns, rows):
    return '\n<!-- ' + marker + ' -->\n| ' + ' | '.join(columns) + ' |\n| ' + ' | '.join('---' for _ in columns) + ' |\n' + ''.join('| ' + ' | '.join(row) + ' |\n' for row in rows)


def page(module, identity, action):
    return f'''# Selection
| 项目 | 内容 |
| --- | --- |
| 模块 | {module} |
| 主页面编号 | {identity} |
| 页面中文名称 | Selection |
| Design revision | PAGE-R01@test |
| Base revision | PAGE-R00@init |

## 操作与状态
| 操作编号 | 页面/组件 | 详细操作 |
| --- | --- | --- |
| {action} | {identity} | 确认返回 |
'''


class PageRelationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'version/V1/module/USER/page-design/edit_design.md'
        self.target = self.root / 'version/V1/module/PER/page-design/picker_design.md'
        self.source.parent.mkdir(parents=True)
        self.target.parent.mkdir(parents=True)
        self.rid = 'PAGEREL-USER-EMPLOYEE'
        self.row = [self.rid, 'PAGEACT-USER-OPEN', '[PAGE-PER-PICKER](../../PER/page-design/picker_design.md)',
                    'PAGEACT-PER-CONFIRM', 'MODAL', '组织范围与已选 employeeId', 'SINGLE',
                    '返回 employeeId 和 employeeName，仅回填草稿', '保留原值']
        self.source_text = page('USER', 'PAGE-USER-EDIT', 'PAGEACT-USER-OPEN') + table('PAGE-RELATIONS', page_relations.RELATION_COLUMNS, [self.row])
        self.target_text = page('PER', 'PAGE-PER-PICKER', 'PAGEACT-PER-CONFIRM') + table('PAGE-CALLERS', page_relations.CALLER_COLUMNS,
            [[self.rid, '[PAGE-USER-EDIT](../../USER/page-design/edit_design.md)', 'PAGEACT-USER-OPEN']])
        self.source.write_text(self.source_text)
        self.target.write_text(self.target_text)

    def validate(self, pending=None):
        docs = page_documents.discover_module_page_documents(self.source.parent.parent, 'USER', pending)
        return page_documents.validate_page_document_links(self.root, docs, pending)

    def test_valid_cross_module_relation_and_snapshot(self):
        self.assertEqual([], self.validate())
        records = model.simulate_design_snapshots(self.root, 'V1', 'USER')
        self.assertEqual(1, len(records))
        self.assertIn(self.rid, records[0]['resultContent'].decode())

    def test_missing_and_wrong_identity_targets(self):
        self.target.unlink()
        self.assertIn('does not exist', ' '.join(self.validate()))
        self.target.write_text(self.target_text.replace('PAGE-PER-PICKER', 'PAGE-PER-OTHER'))
        self.assertIn('primary ID', ' '.join(self.validate()))

    def test_mentions_do_not_define_target_actions(self):
        self.target.write_text(self.target_text.replace('| PAGEACT-PER-CONFIRM |', '| PAGEACT-PER-OTHER |') + '\nPAGEACT-PER-CONFIRM\n')
        self.assertIn('target action', ' '.join(self.validate()))
        with self.assertRaisesRegex(model.MergeError, 'target action'):
            model.simulate_design_snapshots(self.root, 'V1', 'USER')

    def test_reverse_and_duplicate_contract_fail_closed(self):
        self.target.write_text(page('PER', 'PAGE-PER-PICKER', 'PAGEACT-PER-CONFIRM'))
        self.assertIn('reverse caller', ' '.join(self.validate()))
        self.target.write_text(self.target_text)
        self.source.write_text(self.source_text + '| ' + ' | '.join(self.row) + ' |\n')
        self.assertIn('duplicate relation', ' '.join(self.validate()))

    def test_pending_target_is_used(self):
        self.target.unlink()
        self.assertEqual([], self.validate({self.target: self.target_text.encode()}))
        self.assertTrue(self.validate({self.target: self.target_text.replace('PAGE-PER-PICKER', 'PAGE-PER-OTHER').encode()}))

    def test_duplicate_relation_across_source_pages_including_pending_fails(self):
        other = self.source.with_name('other_design.md')
        other_text = self.source_text.replace('PAGE-USER-EDIT', 'PAGE-USER-OTHER')
        self.assertIn('duplicate relation ID', ' '.join(self.validate({other: other_text.encode()})))
        other.write_text(other_text)
        with self.assertRaisesRegex(model.MergeError, 'duplicate relation ID'):
            model.simulate_design_snapshots(self.root, 'V1', 'USER')

    def test_duplicate_target_identity_and_orphan_caller_fail(self):
        duplicate = self.target.with_name('duplicate_design.md')
        duplicate.write_text(self.target_text)
        self.assertIn('duplicate primary PAGE ID', ' '.join(self.validate()))
        duplicate.unlink()
        self.target.write_text(self.target_text.replace(self.rid, 'PAGEREL-USER-ORPHAN'))
        docs = page_documents.discover_module_page_documents(self.target.parent.parent, 'PER')
        self.assertTrue(page_documents.validate_page_document_links(self.root, docs))

    def test_invalid_enums_paths_and_empty_contract(self):
        for old, new in [('MODAL', 'POPUP'), ('SINGLE', 'maybe'), ('保留原值', '待确认'),
                         ('../../PER/page-design/picker_design.md', '/tmp/picker_design.md'),
                         ('../../PER/page-design/picker_design.md', '../../../../../../outside.md')]:
            with self.subTest(new=new):
                self.source.write_text(self.source_text.replace(old, new))
                self.assertTrue(self.validate())

    def test_local_component_and_multiple_callers(self):
        local = page('USER', 'PAGE-USER-EDIT', 'PAGEACT-USER-OPEN')
        local += '| PAGEACT-USER-CONFIRM | PAGE-USER-EDIT | 确认员工 |\n'
        row = list(self.row)
        row[2:4] = ['[PAGE-USER-EDIT](edit_design.md)', 'PAGEACT-USER-CONFIRM']
        local += table('PAGE-RELATIONS', page_relations.RELATION_COLUMNS, [row])
        local += table('PAGE-CALLERS', page_relations.CALLER_COLUMNS,
                       [[self.rid, '[PAGE-USER-EDIT](edit_design.md)', 'PAGEACT-USER-OPEN']])
        self.source.write_text(local)
        self.assertEqual([], self.validate())
        self.source.write_text(self.source_text)
        second = self.source.with_name('second_design.md')
        second.write_text(self.source_text.replace('PAGE-USER-EDIT', 'PAGE-USER-SECOND').replace(self.rid, 'PAGEREL-USER-SECOND'))
        self.target.write_text(self.target_text + '| PAGEREL-USER-SECOND | [PAGE-USER-SECOND](../../USER/page-design/second_design.md) | PAGEACT-USER-OPEN |\n')
        self.assertEqual([], self.validate())
        target_docs = page_documents.discover_module_page_documents(self.target.parent.parent, 'PER')
        self.assertEqual([], page_documents.validate_page_document_links(self.root, target_docs))

    def test_legacy_and_fenced_examples_remain_readable(self):
        self.source.write_text(page('USER', 'PAGE-USER-EDIT', 'PAGEACT-USER-OPEN'))
        self.assertEqual([], self.validate())
        self.source.write_text(self.source.read_text() + '\n```markdown\n' + self.source_text + '\n```\n')
        self.assertEqual([], page_relations.validate_relations(self.source, self.source.read_text(), self.root))

    def case_block(self):
        trace = self.rid + ' PAGE-USER-EDIT PAGE-PER-PICKER PAGEACT-USER-OPEN PAGEACT-PER-CONFIRM'
        return table('TEST-CASE-TRACE', ('对象', '稳定引用', '来源文档'), [['关联', trace, 'requirement.md']]) + table(
            'TEST-CASE-PAGE-RELATIONS', page_relations.CASE_COLUMNS,
            [[self.rid, '[PAGE-USER-EDIT](../../module/USER/page-design/edit_design.md)', '选择 E0007 并确认', 'employeeId=EMP-007；姓名=张三']])

    def test_case_binds_both_sides_and_return_assertion(self):
        path = self.root / 'version/V1/requirement/REQ-USER/test_case.md'
        block = self.case_block()
        self.assertEqual([], page_relations.validate_case_relations(path, block, self.root))
        for old, new in [(' PAGEACT-PER-CONFIRM', ''), ('employeeId=EMP-007；姓名=张三', '正确'),
                         ('PAGEREL-USER-EMPLOYEE', 'PAGEREL-USER-MISSING')]:
            with self.subTest(new=new):
                self.assertTrue(page_relations.validate_case_relations(path, block.replace(old, new), self.root))
        self.assertTrue(page_relations.validate_case_relations(path, block.split('<!-- TEST-CASE-PAGE-RELATIONS -->')[0], self.root))

    def test_json_assertions_are_concrete_but_placeholders_are_not(self):
        path = self.root / 'version/V1/requirement/REQ-USER/test_case.md'
        block = self.case_block()
        current = 'employeeId=EMP-007；姓名=张三'
        for value in ['回填对象精确等于 {"employeeId":"EMP-007","employeeName":"张三"}',
                      '`employeeId` 等于 EMP-007']:
            self.assertEqual([], page_relations.validate_case_relations(path, block.replace(current, value), self.root))
        for value in ['TODO', 'TBD', '待确认', '`todo`', '**正常**', '{EXPECTED_VALUE}']:
            with self.subTest(value=value):
                self.assertTrue(page_relations.validate_case_relations(path, block.replace(current, value), self.root))

    def test_case_ready_integrates_relation_validation_but_draft_does_not(self):
        path = self.root / 'version/V1/requirement/REQ-USER/test_case.md'
        path.parent.mkdir(parents=True)
        (path.parent / 'requirement.md').write_text('# Requirement')
        text = '# Test\n> Requirement：`REQ-USER`\n> 状态：测试设计完成\n'
        text += '\n'.join(case_doc.REQUIRED_HEADINGS)
        text += '\n<!-- TEST-CASE: CASE-USER-001 -->\n## CASE-USER-001\n' + self.case_block()
        for marker, columns in case_doc.TABLE_CONTRACTS.items():
            if marker != 'TEST-CASE-TRACE':
                text += table(marker, columns, [['value' for _ in columns]])
        path.write_text(text.replace('PAGEACT-PER-CONFIRM', 'PAGEACT-PER-WRONG'))
        self.assertTrue(any('Case trace is missing' in error for error in case_doc.validate_document(path, 'ready', self.root).errors))
        self.assertFalse(any('page relation' in error for error in case_doc.validate_document(path, 'structure', self.root).errors))


if __name__ == '__main__':
    unittest.main()
