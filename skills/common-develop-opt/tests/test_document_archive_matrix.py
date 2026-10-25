"""Real lightweight archive gates through the canonical wk dispatcher."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import wk
from archive_fixture import ArchiveFixture, merge_docs, simple_yaml, work_mode


class DocumentArchiveMatrixTests(ArchiveFixture):
    def write_page(self, filename, page_id, name, link=''):
        page_dir = (
            self.root / 'version' / self.scenario.version / 'module'
            / self.scenario.module / 'page-design'
        )
        page_dir.mkdir(parents=True, exist_ok=True)
        linked = f'\n[验收]({link})\n' if link else ''
        path = page_dir / filename
        path.write_text(
            f'# {name}页面业务行为设计\n\n'
            '## 1. 设计信息\n\n'
            '| 项目 | 内容 |\n'
            '| --- | --- |\n'
            f'| 模块 | {self.scenario.module} |\n'
            f'| 主页面编号 | {page_id} |\n'
            f'| 页面中文名称 | {name} |\n'
            '| Design revision | PAGE-R01@test |\n'
            '| Base revision | PAGE-R00@init |\n'
            f'{linked}',
            encoding='utf-8',
        )
        return path

    def archive(self, *options, agent='ProjectManagerAgent'):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wk.main([
                'document-archive', '--doc-root', str(self.root),
                '--version', self.scenario.version, '--module', self.scenario.module,
                '-g', agent, '--json', *options,
            ])
        text = out.getvalue()
        start = text.find('{')
        payload = json.loads(text[start:]) if start >= 0 else None
        return code, payload, text, err.getvalue()

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and p.suffix != '.lock'}

    @property
    def plan_path(self):
        return merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module) / 'merge_plan.yaml'

    @property
    def task_dir(self):
        return self.root / 'version' / self.scenario.version / 'task' / 'ARCHIVE-GATES'

    def prepare(self, *, closed=False, ready=True):
        work_path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode_transaction(
            self.root, self.scenario.version, work_path, work_mode.WorkMode(lightweight=True),
            sync_tasks=False, agent='ProjectManagerAgent',
        )
        self.call(merge_docs.long_task.command_init, argparse.Namespace(
            doc_root=str(self.root), version=self.scenario.version, target='ARCHIVE-GATES',
            reuse=False, agent='ProjectManagerAgent',
        ))
        self.call(merge_docs.long_task.command_sync_work_mode, argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        ))
        if closed:
            work_mode.apply_mode_transaction(
                self.root, self.scenario.version, work_path, work_mode.WorkMode(),
                sync_tasks=False, agent='ProjectManagerAgent',
            )
            self.call(merge_docs.long_task.command_sync_work_mode, argparse.Namespace(
                task_dir=str(self.task_dir), agent='ProjectManagerAgent',
            ))
        result = self.archive()
        self.assertEqual(0, result[0], result)
        self.assertEqual('PREVIEW', result[1]['status'])
        self.assertEqual('LIGHTWEIGHT_CLOSED' if closed else 'LIGHTWEIGHT', result[1]['mode'])
        plan = simple_yaml.load(self.plan_path)
        if ready:
            plan = self.complete_plan(plan)
            simple_yaml.dump(plan, self.plan_path)
        return plan

    def assert_archive(self, *, closed=False, audit=False):
        plan_before = self.plan_path.read_bytes()
        ledger = (self.task_dir / 'task_events.jsonl').read_bytes()
        result = self.archive('--apply', *(['--audit-bundle'] if audit else []))
        self.assertEqual(0, result[0], result)
        self.assertEqual('APPLIED', result[1]['status'])
        self.assertEqual(0, result[1]['archiveStatus'])
        self.assertEqual('LIGHTWEIGHT_CLOSED' if closed else 'LIGHTWEIGHT', result[1]['mode'])
        self.assertEqual(plan_before, self.plan_path.read_bytes())
        self.assertEqual(ledger, (self.task_dir / 'task_events.jsonl').read_bytes())
        manifest = simple_yaml.load(self.root / 'archive_manifest.yaml')
        self.assertEqual(1, len(manifest['archives']))
        ref = manifest['archives'][0]
        archive_path = self.root / ref['manifestRef']
        knowledge = json.loads(archive_path.read_bytes())
        gate = json.loads((self.root / knowledge['gate']['ref']).read_bytes())
        self.assertEqual('PASSED', gate['status'])
        self.assertEqual(ref['resultDigest'], gate['resultDigest'])
        for name in work_mode.lightweight_archive_gate_keys():
            self.assertEqual('NOT_APPLICABLE', gate['archiveGate']['gates'][name]['status'])
        self.assertIn(f'docs/module/{self.scenario.module}/{self.scenario.module}_design.md', knowledge['documents'])
        for group in knowledge['auditRefs'].values():
            for item in group:
                self.assertEqual(item['digest'], hashlib.sha256((self.root / item['path']).read_bytes()).hexdigest())
        portals = merge_docs.document_portal.validate_existing(self.root, versions=[self.scenario.version])
        self.assertTrue(portals['valid'], portals)
        self.assertEqual(audit, (archive_path.parent / 'audit_bundle.json').exists())
        return archive_path

    def test_active_lightweight_archive_apply_and_repeat(self):
        self.prepare()
        self.assert_archive()
        before = self.snapshot()
        result = self.archive('--apply')
        self.assertEqual(0, result[0], result)
        self.assertEqual('APPLIED', result[1]['status'])
        self.assertEqual(before, self.snapshot())

    def test_closed_lightweight_archive_apply_preserves_waivers(self):
        self.prepare(closed=True)
        self.assert_archive(closed=True)

    def test_archive_audit_bundle_and_repeat(self):
        self.prepare()
        path = self.assert_archive(audit=True)
        bundle = json.loads((path.parent / 'audit_bundle.json').read_bytes())
        self.assertEqual(json.loads(path.read_bytes()), bundle['manifest'])
        self.assertTrue(bundle['indexes'])
        before = self.snapshot()
        self.assertEqual(0, self.archive('--apply', '--audit-bundle')[0])
        self.assertEqual(before, self.snapshot())

    def test_archive_recovers_audit_export_from_existing_manifest(self):
        self.prepare()
        path = self.assert_archive()
        self.plan_path.unlink()
        before = self.snapshot()
        result = self.archive('--apply', '--audit-bundle')
        self.assertEqual(0, result[0], result)
        after = self.snapshot()
        self.assertEqual({(path.parent / 'audit_bundle.json').relative_to(self.root).as_posix()}, set(after) - set(before))
        self.assertTrue(all(after[k] == v for k, v in before.items()))

    def test_archive_rejects_incomplete_or_stale_plan_without_writes(self):
        plan = self.prepare(ready=False)
        before = self.snapshot()
        result = self.archive('--apply')
        self.assertNotEqual(0, result[0], result)
        self.assertIn('archiveGate.status must be READY', result[2] + result[3])
        self.assertEqual(before, self.snapshot())
        plan = self.complete_plan(plan)
        simple_yaml.dump(plan, self.plan_path)
        change = simple_yaml.load(self.change)
        change['changeSet']['reason'] += ' changed after preview'
        simple_yaml.dump(change, self.change)
        before = self.snapshot()
        result = self.archive('--apply')
        self.assertNotEqual(0, result[0], result)
        self.assertIn('stale', result[2] + result[3])
        self.assertEqual(before, self.snapshot())

    def test_archive_rejects_missing_evidence_without_writes(self):
        plan = self.prepare()
        gate = next(gate for gate in plan['archiveGate']['gates'].values() if gate['status'] == 'PASSED')
        gate['evidenceIds'] = ['EVID-NOT-REGISTERED']
        simple_yaml.dump(plan, self.plan_path)
        before = self.snapshot()
        result = self.archive('--apply')
        self.assertNotEqual(0, result[0], result)
        self.assertIn('EVID-NOT-REGISTERED', result[2] + result[3])
        self.assertEqual(before, self.snapshot())

    def test_archive_rejects_wrong_agent_without_writes(self):
        self.prepare()
        before = self.snapshot()
        result = self.archive('--apply', agent='DevelopAgent')
        self.assertNotEqual(0, result[0], result)
        self.assertEqual(before, self.snapshot())

    def test_two_page_documents_have_distinct_archive_identities_and_targets(self):
        module_source = (
            self.root / 'version' / self.scenario.version / 'module' / self.scenario.module
        )
        requirement = (
            self.root / 'version' / self.scenario.version / 'requirement'
            / 'REQ-CAT-001' / 'requirement.md'
        )
        requirement.parent.mkdir(parents=True, exist_ok=True)
        requirement.write_text(
            '<a id="ac-req-cat-001-001"></a>\n\n'
            '### AC-REQ-CAT-001-001 商品查询可用\n',
            encoding='utf-8',
        )
        self.write_page(
            'product_list_design.md',
            'PAGE-CAT-PRODUCT-LIST',
            '商品列表',
            '../../../requirement/REQ-CAT-001/requirement.md#ac-req-cat-001-001',
        )
        self.write_page('product_detail_design.md', 'PAGE-CAT-PRODUCT-DETAIL', '商品详情')

        records = merge_docs.simulate_design_snapshots(
            self.root,
            self.scenario.version,
            self.scenario.module,
        )
        page_records = [item for item in records if item['artifactType'] == 'page_behavior']

        self.assertEqual(
            {'PAGE-CAT-PRODUCT-LIST', 'PAGE-CAT-PRODUCT-DETAIL'},
            {item['stableId'] for item in page_records},
        )
        self.assertEqual(
            {
                'docs/module/CAT/design/pages/product_list_design.md',
                'docs/module/CAT/design/pages/product_detail_design.md',
            },
            {item['targetRef'] for item in page_records},
        )
        list_record = next(item for item in page_records if item['stableId'] == 'PAGE-CAT-PRODUCT-LIST')
        archived_text = list_record['resultContent'].decode('utf-8')
        self.assertIn(
            '../../../../../version/V_3.4/requirement/REQ-CAT-001/requirement.md#ac-req-cat-001-001',
            archived_text,
        )


if __name__ == '__main__':
    unittest.main()
