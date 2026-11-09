"""Trial recovery/identity tests; never stop real user services."""
import json
import io
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch, Mock

from test_tools import tool


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.module = tool("trial_board")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_old_identity_requires_exact_entry_and_port(self):
        info = {"pid": 100, "state": "S", "argv": ["python", self.module.ENTRY, "--port", "8080"]}
        self.module.validate_old(info)
        for args in (["python", "unrelated.py", "--port", "8080"],
                     ["python", self.module.ENTRY, "--port", "18080"],
                     ["python", self.module.ENTRY + ".backup", "--port", "8080"]):
            with self.subTest(args=args), self.assertRaises(self.module.TrialError):
                self.module.validate_old(dict(info, argv=args))

    def test_stat_parser_handles_parentheses_and_reads_start_time(self):
        folder = self.root / "123"
        folder.mkdir()
        fields = ["S", "7"] + ["0"] * 17 + ["9876"]
        (folder / "stat").write_text("123 (process ) name) " + " ".join(fields))
        (folder / "cmdline").write_bytes(b"python\0a.py\0")
        info = self.module.proc_info(123, self.root)
        self.assertEqual((info["ppid"], info["start"], info["argv"]), (7, "9876", ["python", "a.py"]))

    def test_only_descendant_tree_is_selected(self):
        table = {1:{"ppid":0}, 10:{"ppid":1}, 11:{"ppid":10}, 12:{"ppid":11}, 20:{"ppid":1}}
        self.assertEqual(self.module.descendants(10, table), {10,11,12})

    def test_pid_reuse_is_rejected_without_sending_signals(self):
        old = {"pid":123, "start":"A", "argv":["python"]}
        with patch.object(self.module.os, "pidfd_open", create=True, return_value=42), \
             patch.object(self.module, "proc_info", return_value=dict(old, start="B")), \
             patch.object(self.module.os, "close") as close, \
             patch.object(self.module.signal, "pidfd_send_signal", create=True) as send:
            with self.assertRaises(self.module.TrialError): self.module.ProcessRef(old)
            close.assert_called_once_with(42)
            send.assert_not_called()

    def test_signal_is_sent_to_pidfd_not_numeric_pid(self):
        old = {"pid":123, "start":"A", "argv":["python"]}
        with patch.object(self.module.os, "pidfd_open", create=True, return_value=42), \
             patch.object(self.module, "proc_info", return_value=old), \
             patch.object(self.module.os, "close"), \
             patch.object(self.module.signal, "pidfd_send_signal", create=True) as send:
            process = self.module.ProcessRef(old)
            process.terminate()
            send.assert_called_once_with(42, signal.SIGTERM, None, 0)
            process.close()

    def fake(self, failure=None):
        module, root = self.module, self.root
        class Fake:
            def __init__(self): self.calls, self.results, self.root, self.touched = [], [], root, False
            def call(self, name):
                self.calls.append(name)
                if name == failure: raise module.TrialError(name + " failed")
            def preflight(self): self.call("preflight")
            def stop_old(self): self.touched = True; self.call("stop_old")
            def run_candidate(self): self.call("run_candidate")
            def stop_candidate(self): self.call("stop_candidate")
            def restore_old(self): self.call("restore_old")
            def close(self): self.call("close")
        return Fake()

    def test_success_stops_candidate_before_restoring_old(self):
        trial = self.fake()
        self.assertEqual(self.module.execute(trial), 0)
        self.assertEqual(trial.calls, ["preflight", "stop_old", "run_candidate", "stop_candidate", "restore_old", "close"])
        self.assertTrue(json.loads((self.root / "trial-results.json").read_text())["ok"])

    def test_generation_failure_still_restores_old(self):
        trial = self.fake("run_candidate")
        self.assertEqual(self.module.execute(trial), 1)
        self.assertEqual(trial.calls[-3:], ["stop_candidate", "restore_old", "close"])
        report = json.loads((self.root / "trial-results.json").read_text())
        self.assertIn("run_candidate", report["error"])
        self.assertIsNone(report["restore_error"])

    def test_preflight_failure_does_not_touch_old_service(self):
        trial = self.fake("preflight")
        self.assertEqual(self.module.execute(trial), 1)
        self.assertFalse(trial.touched)
        self.assertNotIn("run_candidate", trial.calls)

    def test_candidate_stop_failure_does_not_skip_restore_attempt(self):
        trial = self.fake("stop_candidate")
        self.assertEqual(self.module.execute(trial), 1)
        self.assertIn("restore_old", trial.calls)
        self.assertIn("Candidate stop", json.loads((self.root / "trial-results.json").read_text())["restore_error"])

    def test_restore_failure_never_reports_success(self):
        trial = self.fake("restore_old")
        self.assertEqual(self.module.execute(trial), 1)
        self.assertFalse(json.loads((self.root / "trial-results.json").read_text())["ok"])

    def test_partial_stop_is_finished_before_restart(self):
        trial = self.module.BoardTrial(self.root)
        trial.touched = True
        trial.original_pid_text = "100\n"
        pidfile = self.root / "old.pid"
        pidfile.write_text("999\n")
        class Ref:
            stopped = False
            def alive(self): return not self.stopped
            def terminate(self): self.stopped = True
        ref = Ref(); trial.refs = [ref]
        with patch.object(self.module, "PIDFILE", pidfile), \
             patch.object(self.module, "require_exclusive_runtime"):
            # Reject concurrent external PID-file change after old cleanup.
            with self.assertRaisesRegex(self.module.TrialError, "changed externally"):
                trial.restore_old()
        self.assertTrue(ref.stopped)

    def test_restore_uses_captured_command_environment_and_only_model_pidfile(self):
        trial = self.module.BoardTrial(self.root)
        trial.touched = True
        trial.original_pid_text = "100\n"
        trial.old_env = {"PATH":"captured", "LD_LIBRARY_PATH":"original"}
        trial.old_cwd = "original-directory"
        trial.old_log = Mock()
        trial.master = Mock()
        trial.master.info = {"argv":["python", self.module.ENTRY, "--port", "8080"]}
        ref = Mock(); ref.alive.return_value = False
        trial.refs = [ref]
        pidfile = self.root / "old.pid"
        pidfile.write_text("100\n")
        process = Mock(pid=321)
        process.poll.return_value = None
        with patch.object(self.module, "PIDFILE", pidfile), \
             patch.object(self.module, "require_exclusive_runtime"), \
             patch.object(self.module, "tags_ready", return_value=True), \
             patch.object(self.module.subprocess, "Popen", return_value=process) as spawn:
            trial.restore_old()
        self.assertEqual(pidfile.read_text(), "321\n")
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], trial.master.info["argv"])
        self.assertEqual(kwargs["env"], trial.old_env)
        self.assertEqual(kwargs["cwd"], trial.old_cwd)
        self.assertEqual(kwargs["stdout"], trial.old_log)

    def test_incomplete_stream_is_not_reported_as_a_success(self):
        partial = {"model":"qwen2-0.5b", "message":{"content":"partial"}, "done":False}
        body = io.BytesIO((json.dumps(partial) + "\n").encode())
        with patch.object(self.module.OPENER, "open", return_value=body):
            with self.assertRaisesRegex(self.module.TrialError, "without done"):
                self.module.chat_once("http://127.0.0.1:18080", "qwen2-0.5b")

    def test_stream_completion_also_checks_real_server_state(self):
        chunks = [{"model":"qwen2-0.5b", "message":{"content":"你好"}, "done":False},
                  {"model":"qwen2-0.5b", "message":{"content":""}, "done":True}]
        body = io.BytesIO(b"".join((json.dumps(c) + "\n").encode() for c in chunks))
        with patch.object(self.module.OPENER, "open", return_value=body), \
             patch.object(self.module, "json_get", return_value={"loaded_model":"qwen2-0.5b", "hardware_inference_verified":False}):
            with self.assertRaisesRegex(self.module.TrialError, "did not confirm"):
                self.module.chat_once("http://127.0.0.1:18080", "qwen2-0.5b")
