#!/usr/bin/env python3
"""Regression tests for the task event reducer and current manifest."""
from __future__ import annotations
import argparse, contextlib, io, json, tempfile, unittest
from pathlib import Path
import auto_remediation, long_task
from _long_task import task_events

class TaskEventsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.doc_root=Path(self.temp.name)/"project_doc"
        args=argparse.Namespace(doc_root=str(self.doc_root),version="V_TEST",target="MOD-EVT",reuse=False,request_intake="",agent="ProjectManagerAgent")
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(0,long_task.command_init(args))
        self.task_dir=self.doc_root/"version"/"V_TEST"/"task"/"MOD-EVT"
    def tearDown(self): self.temp.cleanup()
    def cache_state(self): return json.loads(task_events.state_cache_path(self.task_dir).read_text(encoding="utf-8"))

    def test_init_uses_v3_ledger_and_cache_only_state(self):
        self.assertTrue((self.task_dir/"task_events.jsonl").is_file())
        self.assertTrue((self.task_dir/"task.json").is_file())
        self.assertTrue(task_events.state_cache_path(self.task_dir).is_file())
        self.assertFalse((self.task_dir/"task_state.md").exists())
        self.assertFalse((self.task_dir/"review_issues.md").exists())
        self.assertFalse((self.task_dir/"task_attempts.md").exists())
        self.assertFalse((self.task_dir/"stage_outcomes.md").exists())
        state=self.cache_state(); self.assertEqual("EVENT_LEDGER_V3",state["state"]["storageMode"])
        self.assertGreaterEqual(state["state"]["eventSeq"],1)

    def test_attempt_hash_chain_and_reducer(self):
        before=task_events.read_events(self.task_dir)
        attempt={"attempt_id":"ATT-1","task_id":"DEV-01","status":"RUNNING","attempt_no":1,"started_at":"2026-08-15T00:00:00Z"}
        text,_,events=task_events.append_event_text(self.task_dir,"ATTEMPT_STARTED","ATT-1",{"attempt":attempt})
        (self.task_dir/"task_events.jsonl").write_text(text,encoding="utf-8")
        done=dict(attempt,status="PASSED",completed_at="2026-08-15T00:01:00Z",output_revision="R01",evidence_ids=["EVD-1"])
        text,_,events=task_events.append_event_text(self.task_dir,"ATTEMPT_FINISHED","ATT-1",{"attempt":done})
        (self.task_dir/"task_events.jsonl").write_text(text,encoding="utf-8")
        loaded=task_events.read_events(self.task_dir)
        self.assertEqual(list(range(1,len(loaded)+1)),[e["seq"] for e in loaded])
        self.assertEqual(loaded[-2]["digest"],loaded[-1]["prev_digest"])
        reduced=task_events.reduce_events(loaded); self.assertEqual("PASSED",reduced["attempts"][-1]["status"])
        self.assertGreater(len(loaded),len(before))

    def test_hash_corruption_is_rejected(self):
        lines=(self.task_dir/"task_events.jsonl").read_text(encoding="utf-8").splitlines()
        event=json.loads(lines[-1]); event["payload"]["tampered"]=True; lines[-1]=json.dumps(event,separators=(",",":"))
        (self.task_dir/"task_events.jsonl").write_text("\n".join(lines)+"\n",encoding="utf-8")
        with self.assertRaises(task_events.TaskEventError): task_events.read_events(self.task_dir)

    def test_state_cache_can_be_deleted_and_rebuilt_from_reducer(self):
        task_events.state_cache_path(self.task_dir).unlink()
        state=long_task.runtime_state(self.task_dir)
        self.assertEqual("MOD-EVT",state["target_id"])
        doc,state2=long_task.read_task_state_cache(self.task_dir)
        self.assertIn('"authority": "DERIVED"', doc); self.assertEqual(state["target_id"],state2["target_id"])

    def test_subsequent_state_changes_use_nested_path_patch(self):
        before=long_task.runtime_state(self.task_dir)
        desired=json.loads(json.dumps(before))
        desired["collaboration_reviews"]["design"]["status"]="IN_REVIEW"
        patch=task_events.state_patch_spec(
            task_events.reduce_events(task_events.read_events(self.task_dir)),desired,
            timestamp="2026-09-01T00:00:00Z",source="test",
        )
        self.assertIsNotNone(patch)
        payload=patch["payload"]
        self.assertNotIn("set",payload)
        self.assertEqual(
            "IN_REVIEW",
            payload["set_paths"]["/collaboration_reviews/design/status"],
        )
        text,_,events=task_events.append_many_text(self.task_dir,[patch])
        reduced=task_events.reduce_events(events)
        self.assertEqual("IN_REVIEW",reduced["state_authority"]["collaboration_reviews"]["design"]["status"])
        self.assertLess(len(json.dumps(payload,ensure_ascii=False)),300)

    def test_explicit_event_compaction_preserves_reducer_and_sequence(self):
        before_events=task_events.read_events(self.task_dir)
        before=task_events.reduce_events(before_events)
        plan=task_events.prepare_compaction(self.task_dir,force=True,timestamp="2026-09-01T00:00:00Z")
        self.assertEqual("READY",plan["status"])
        self.assertGreater(plan["snapshotSeq"],plan["lastSeq"])
        preview=argparse.Namespace(
            task_dir=str(self.task_dir),threshold_bytes=task_events.DEFAULT_COMPACTION_THRESHOLD_BYTES,
            force=True,plan_digest="",apply=False,json=True,agent="ProjectManagerAgent",
        )
        output=io.StringIO()
        with contextlib.redirect_stdout(output): self.assertEqual(0,long_task.command_compact_events(preview))
        preview_payload=json.loads(output.getvalue())
        self.assertFalse(preview_payload["applied"])
        apply=argparse.Namespace(**{**vars(preview),"apply":True,"plan_digest":preview_payload["planDigest"]})
        output=io.StringIO()
        with contextlib.redirect_stdout(output): self.assertEqual(0,long_task.command_compact_events(apply))
        result=json.loads(output.getvalue())
        self.assertEqual("COMPACTED",result["status"])
        self.assertEqual("EVENT_LEDGER_V4_SEGMENTED",result["storageMode"])
        active=task_events.read_events(self.task_dir)
        self.assertEqual(task_events.SNAPSHOT_EVENT_TYPE,active[0]["type"])
        self.assertEqual(before["state_authority"],task_events.reduce_events(active)["state_authority"])
        self.assertTrue((self.task_dir/result["archiveRef"]).is_file())
        history=task_events.read_event_history(self.task_dir)
        self.assertEqual([*range(1,active[0]["seq"]+1)],[item["seq"] for item in history])
        text,event,events=task_events.append_event_text(
            self.task_dir,"TASK_RUNTIME_SET","DEV-01",{"runtime":{"status":"RUNNING"}},
        )
        self.assertEqual(active[-1]["seq"]+1,event["seq"])
        (self.task_dir/"task_events.jsonl").write_text(text,encoding="utf-8")
        self.assertEqual("RUNNING",task_events.reduce_events(task_events.read_events(self.task_dir))["task_runtime"]["DEV-01"]["status"])

    def test_event_compaction_fails_closed_when_archive_is_tampered(self):
        plan=task_events.prepare_compaction(self.task_dir,force=True,timestamp="2026-09-01T00:00:00Z")
        archive=self.task_dir/plan["archiveRef"]
        archive.parent.mkdir(parents=True,exist_ok=True)
        archive.write_bytes(plan["_archiveBytes"])
        (self.task_dir/"task_events.jsonl").write_text(plan["_ledgerText"],encoding="utf-8")
        archive.write_bytes(archive.read_bytes()+b"x")
        with self.assertRaises(task_events.TaskEventError): task_events.read_events(self.task_dir)

    def test_task_runtime_materializes_without_mutating_static_plan(self):
        static=[{"id":"DEV-01","title":"x","objective":"x","phase":"development","depends_on":[],"owner_agent":"DevelopAgent","reviewer_agents":[],"input_revisions":{},"allowed_files":[],"acceptance_trace_ids":[],"flow_step_refs":[],"flow_refs":[],"validation_commands":[],"expected_results":[],"stop_conditions":[],"max_attempts":3,"logical_task_id":"DEV-01","feature_id":"F","iteration_id":"I1","iteration_no":1,"supersedes_iteration_id":"","revision_reason":"","risk_triggers":[]}]
        reduced=task_events.reduce_events(task_events.read_events(self.task_dir))
        text,_,events=task_events.append_many_text(self.task_dir,[{"type":"TASK_RUNTIME_SET","entity_id":"DEV-01","payload":{"runtime":{"status":"PASSED","attempts":1,"output_revision":"R01","validation_evidence_ids":["EVD-1"]}}}])
        materialized=task_events.materialize_tasks(static,task_events.reduce_events(events))[0]
        self.assertEqual("PASSED",materialized["status"]); self.assertEqual("R01",materialized["output_revision"])
        self.assertNotIn("status",static[0]); self.assertNotIn("output_revision",static[0])

    def test_issue_lifecycle_is_reducer_owned(self):
        issue={"id":"ISSUE-1","status":"OPEN","severity":"P1","phase":"design","artifact_revision":"D1","description":"x","owner_agent":"DevelopAgent","reported_by":"ArchitectureReviewAgent","created_at":"T","updated_at":"T","evidence_ids":[]}
        specs=[task_events.issue_event_spec("ISSUE_OPENED",issue,timestamp="T",source="test")]
        closed=dict(issue,status="CLOSED",updated_at="T2")
        specs.append(task_events.issue_event_spec("ISSUE_CLOSED",closed,timestamp="T2",source="test"))
        text,_,events=task_events.append_many_text(self.task_dir,specs); (self.task_dir/"task_events.jsonl").write_text(text,encoding="utf-8")
        reduced=task_events.reduce_events(events); self.assertEqual("CLOSED",reduced["issues"][0]["status"])
        state=task_events.project_state({},reduced,synced_at="T2"); self.assertNotIn("ISSUE-1",state["open_issue_ids"])

    def test_issue_event_command_drives_assignment_remediation_verification_and_close(self):
        issue={
            "id":"ISSUE-LIFE-1","issue_type":"REVIEW_FINDING","axis":"spec","severity":"P1","confidence":"HIGH",
            "status":"OPEN","phase":"requirement_confirmation","round":"REQUIREMENT_CONFIRMATION-I001",
            "artifact_revision":"REQCONF-R01","raised_by_agent":"RequirementAnalysisAgent","owner_agent":"RequirementConfirmationAgent",
            "title":"x","description":"x","impact":"x","motivating_evidence":[],"question":"x","question_to":"RequirementConfirmationAgent",
            "responses":[],"recommendation":"fix","affected_artifacts":[],"affected_trace_ids":[],"decision":"",
            "resolution_revision":"","resolution_evidence":[],"verified_by_agent":"","verified_at":"","defer_reason":"",
        }
        state_document,state=long_task.read_task_state_cache(self.task_dir)
        payloads,_,_=long_task.append_task_events_payloads(
            self.task_dir,long_task.task_state_path(self.task_dir),state_document,state,
            [task_events.issue_event_spec("ISSUE_OPENED",issue,timestamp="2026-08-15T00:00:00Z",source="test")],
            synced_at="2026-08-15T00:00:00Z",
        )
        long_task.transactional_text_write(payloads)
        def transition(agent,event_type,**kwargs):
            args=argparse.Namespace(
                task_dir=str(self.task_dir),issue_id="ISSUE-LIFE-1",event_type=event_type,
                owner_agent=kwargs.get("owner_agent",""),resolution_revision=kwargs.get("resolution_revision",""),
                evidence_id=kwargs.get("evidence_id",[]),reason=kwargs.get("reason",""),agent=agent,
            )
            with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(0,long_task.command_issue_event(args))
        transition("ProjectManagerAgent","assigned",owner_agent="RequirementConfirmationAgent")
        transition("RequirementConfirmationAgent","remediated",resolution_revision="REQCONF-R02",evidence_id=["EVD-1"])
        transition("RequirementAnalysisAgent","verified",evidence_id=["EVD-2"])
        transition("ProjectManagerAgent","closed")
        current={item["id"]:item for item in long_task.runtime_issues(self.task_dir)}["ISSUE-LIFE-1"]
        self.assertEqual("CLOSED",current["status"])
        self.assertEqual("RequirementAnalysisAgent",current["verified_by_agent"])
        self.assertEqual("REQCONF-R02",current["resolution_revision"])
        types=[e["type"] for e in task_events.read_events(self.task_dir) if e.get("entity_id")=="ISSUE-LIFE-1"]
        self.assertEqual(["ISSUE_OPENED","ISSUE_ASSIGNED","ISSUE_REMEDIATED","ISSUE_VERIFIED","ISSUE_CLOSED"],types)

    def test_auto_remediation_is_in_ledger_and_projection(self):
        args=argparse.Namespace(task_dir=str(self.task_dir),task_id="DEV-01",revision="R01",evidence_ref=[])
        auto_remediation._record_task_event(args,"AUTO_REMEDIATION_DECISION",{"decision":"AUTO_FIX","signal":"p1_open"})
        self.assertEqual("AUTO_REMEDIATION_DECISION",task_events.read_events(self.task_dir)[-1]["type"])
        self.assertEqual("AUTO_FIX",long_task.runtime_state(self.task_dir)["auto_remediation"]["decision"])

if __name__=="__main__": unittest.main()
