"""roll-out when it is stopped: signals undo the repository in hand, and a run record lets a later run undo a dead run."""
import fcntl
import json
import os
import signal
import time

from test_rollout import (HOOK_BG_HANG, RollOutBase, VERIFY_FAIL, VERIFY_OK, git, pid_alive, read, wait_until, write)

# Hangs while .git/hang exists, with a background child as a real verify run has; otherwise passes at once.
VERIFY_HANG_IF = ('#!/bin/sh\n[ -f .git/hang ] || exit 0\nsleep 300 &\necho $! > .git/bg-pid\necho $$ > .git/verify-pid\n'
                  'echo started > .git/verify-started\nwait\n')
SUBJECT = "ci: load the shared commit checks at v1.1.0"
RECORD = "commit-checks-roll-out.json"


class RecoveryBase(RollOutBase):
    def git_dir(self, root):
        return os.path.join(root, ".git")

    def record_path(self, root):
        return os.path.join(self.git_dir(root), RECORD)

    def hang(self, root, on=True):
        marker = os.path.join(self.git_dir(root), "hang")
        if on:
            write(marker, "")
        elif os.path.exists(marker):
            os.unlink(marker)

    def wait_marker(self, root, name):
        path = os.path.join(self.git_dir(root), name)
        wait_until(self, lambda: os.path.isfile(path), name)
        return path

    def bg_pid(self, root):
        path = os.path.join(self.git_dir(root), "bg-pid")
        wait_until(self, lambda: os.path.isfile(path) and read(path).strip(), "bg-pid")
        return int(read(path).strip())

    def kill_verify_tree(self, root):
        """Orphans of a killed roll-out: stop the fake verify's group so no test leaves a sleeper behind."""
        path = os.path.join(self.git_dir(root), "verify-pid")
        if os.path.isfile(path):
            try:
                os.killpg(os.getpgid(int(read(path).strip())), signal.SIGKILL)  # the script's group, led by the watcher
            except (OSError, ValueError):
                pass

    def write_record(self, root, start, files=(".commit-checks.json", "scripts/commit-checks"), subjects=(SUBJECT,),
                     default="main", collab=False, pid=2 ** 22 + 1, **more):
        """A record as a dead run would have left it: no process holds its lock."""
        data = {"format": 1, "start": start, "default": default, "collab": collab, "files": list(files),
                "subjects": list(subjects), "pid": pid, "started": time.time() - 600}
        data.update(more)
        write(self.record_path(root), json.dumps(data))


class TestSignalsDuringVerify(RecoveryBase):
    CHECK = ""

    def stopped_run(self, sig, agents="# Agents\n"):
        root = self.make_consumer("alpha", verify=VERIFY_HANG_IF, agents=agents)
        self.hang(root)
        head = self.head(root)
        proc = self.roll_started("v1.1.0", "--apply")
        self.wait_marker(root, "verify-started")
        self.assertTrue(os.path.isfile(self.record_path(root)))  # written before the first edit
        pid = self.bg_pid(root)
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=60)
        return root, head, pid, proc, out, err

    def check_stopped(self, sig, name):
        root, head, pid, proc, out, err = self.stopped_run(sig)
        self.assertEqual(proc.returncode, 2, out + err)
        self.assertNotIn("Traceback", err)
        self.assertRegex(out, r"(?m)^INTERRUPTED    alpha: %s; verification stopped, edits undone \(<?\d+ min\)$" % name)
        self.assertIn("roll-out stopped by %s; not reached: none." % name, out)
        self.assertIn("1 consumer(s); 1 need attention: alpha.", out)
        self.assertUntouched(root, head)
        self.assertFalse(os.path.exists(self.record_path(root)))
        wait_until(self, lambda: not pid_alive(pid), "the background child to be gone")

    def test_sigint(self):
        self.check_stopped(signal.SIGINT, "SIGINT")

    def test_sigterm(self):
        self.check_stopped(signal.SIGTERM, "SIGTERM")

    def test_sighup(self):
        self.check_stopped(signal.SIGHUP, "SIGHUP")

    def test_a_collaborative_repository_is_back_on_its_branch(self):
        root, head, _pid, proc, out, _err = self.stopped_run(signal.SIGTERM, agents="# Agents\n\nCollaborative: use branches and pull requests.\n")
        self.assertEqual(proc.returncode, 2)
        self.assertUntouched(root, head)
        self.assertEqual(git(root, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(git(root, "branch", "--list", "ci/shared-pins"), "")

    def test_the_first_repository_is_reported_when_the_second_is_stopped(self):
        a = self.make_consumer("alpha", verify=VERIFY_OK)
        b = self.make_consumer("beta", verify=VERIFY_HANG_IF)
        self.make_consumer("gamma")
        self.hang(b)
        proc = self.roll_started("v1.1.0", "--apply")
        self.wait_marker(b, "verify-started")
        proc.send_signal(signal.SIGTERM)
        out, _err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertRegex(out, r"(?m)^UPDATED        alpha: .*not pushed")
        self.assertRegex(out, r"(?m)^INTERRUPTED    beta: SIGTERM; verification stopped, edits undone")
        self.assertIn("roll-out stopped by SIGTERM; not reached: gamma.", out)
        self.assertIn("3 consumer(s); 1 need attention: beta.", out)
        self.assertEqual(self.settings(a)["version"], "v1.1.0")
        self.assertEqual(self.settings(b)["version"], "v1.0.0")
        self.assertEqual(git(b, "status", "--porcelain"), "")

    def test_a_stop_before_any_repository_is_reported_and_exits_two(self):
        # The gate is the slow part here; it never ends.
        self.make_consumer("alpha")
        write(os.path.join(self.tmp, "gate.py"), "import os, sys, time\nwith open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gate-started'), 'w') as fh:\n    fh.write('x')\ntime.sleep(300)\n")
        lst = self.write_list({"gates": {"commit-checks": [os.sys.executable, "gate.py", "{tag}", "{pins}"]}})
        proc = self.roll_started("v1.1.0", "--apply", list_path=lst)
        marker = os.path.join(self.tmp, "gate-started")
        wait_until(self, lambda: os.path.isfile(marker), "the gate to start")
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 2, out + err)
        self.assertIn("roll-out stopped by SIGTERM; not reached: none.", out)
        self.assertNotIn("Traceback", err)


class TestKilledOutright(RecoveryBase):
    """SIGKILL cannot be caught: the verify tree must stop anyway, within the stop grace."""
    CHECK = ""

    def killed(self, whole_group):
        root = self.make_consumer("alpha", verify=VERIFY_HANG_IF)
        self.hang(root)
        proc = self.roll_started("v1.1.0", "--apply", new_session=True)  # its own group, so a group kill reaches only roll-out
        self.wait_marker(root, "verify-started")
        self.addCleanup(self.kill_verify_tree, root)
        pid = self.bg_pid(root)
        self.assertTrue(pid_alive(pid))
        if whole_group:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.communicate(timeout=30)
        wait_until(self, lambda: not pid_alive(pid), "the verify tree to stop after roll-out was killed")  # grace is 10 s

    def test_sigkill_to_the_process_group(self):
        self.killed(True)

    def test_sigkill_to_the_python_process_only(self):
        self.killed(False)


class TestStoppedDuringThePush(RecoveryBase):
    CHECK = ""

    def test_the_commits_and_the_record_stay_and_the_next_run_undoes_them(self):
        root = self.make_consumer("alpha")
        remote = git(root, "remote", "get-url", "origin")
        write(os.path.join(remote, "hooks", "pre-receive"), "#!/bin/sh\necho $$ > %s\necho x > %s\nexec sleep 300\n"
              % (os.path.join(self.tmp, "hook-pid"), os.path.join(self.tmp, "hook-started")), 0o755)
        head = self.head(root)
        proc = self.roll_started("v1.1.0", "--apply", "--push")
        marker = os.path.join(self.tmp, "hook-started")
        wait_until(self, lambda: os.path.isfile(marker), "the push to reach the remote hook")
        self.addCleanup(lambda: pid_alive(int(read(os.path.join(self.tmp, "hook-pid")))) and os.kill(int(read(os.path.join(self.tmp, "hook-pid"))), signal.SIGKILL))
        proc.send_signal(signal.SIGTERM)
        out, _err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("INTERRUPTED    alpha: SIGTERM; the commits stay and the push is not confirmed", out)
        self.assertTrue(os.path.isfile(self.record_path(root)))  # not a silent unpushed commit
        self.assertNotEqual(self.head(root), head)
        os.unlink(os.path.join(remote, "hooks", "pre-receive"))  # the remote works again; nothing reached it
        proc = self.roll("v1.1.0", "--apply", "--push")
        self.assertPasses(proc)
        self.assertIn("RESTORED       alpha: undid an earlier run's edits; back at %s" % head[:7], proc.stdout)
        self.assertIn("UPDATED        alpha", proc.stdout)
        self.assertEqual(git(root, "rev-parse", "HEAD~1"), head)  # one new commit, pushed
        self.assertEqual(git(root, "ls-remote", "origin", "refs/heads/main").split()[0], self.head(root))
        self.assertFalse(os.path.exists(self.record_path(root)))


class TestRecordFromADeadRun(RecoveryBase):
    CHECK = ""

    def kill_during_verify(self):
        root = self.make_consumer("alpha", verify=VERIFY_HANG_IF)
        self.hang(root)
        head = self.head(root)
        proc = self.roll_started("v1.1.0", "--apply")
        self.wait_marker(root, "verify-started")
        self.addCleanup(self.kill_verify_tree, root)
        proc.kill()
        proc.communicate()
        self.kill_verify_tree(root)
        self.hang(root, on=False)
        return root, head

    def test_a_dry_run_after_sigkill_reports_and_touches_nothing(self):
        root, head = self.kill_during_verify()
        self.assertEqual(git(root, "status", "--porcelain"), "M  .commit-checks.json\nM  scripts/commit-checks")  # staged edits left behind
        before = read(self.record_path(root))
        proc = self.roll("v1.1.0")
        self.assertBlocks(proc)
        self.assertRegex(proc.stdout, r"INTERRUPTED    alpha: an earlier run \(pid \d+, started \d\d:\d\d\) stopped here and left its edits; "
                                      r"--apply undoes them first \(back to %s\)" % head[:7])
        self.assertEqual(git(root, "status", "--porcelain"), "M  .commit-checks.json\nM  scripts/commit-checks")
        self.assertEqual(read(self.record_path(root)), before)
        self.assertEqual(self.head(root), head)

    def test_apply_after_sigkill_restores_then_updates(self):
        root, head = self.kill_during_verify()
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        lines = proc.stdout.splitlines()
        restored = [i for i, l in enumerate(lines) if l.startswith("RESTORED       alpha: undid an earlier run's edits; back at %s" % head[:7])]
        updated = [i for i, l in enumerate(lines) if l.startswith("UPDATED        alpha")]
        self.assertTrue(restored and updated and restored[0] < updated[0], proc.stdout)
        self.assertFalse(os.path.exists(self.record_path(root)))
        self.assertEqual(git(root, "rev-list", "--count", head + "..HEAD"), "1")
        self.assertEqual(git(root, "status", "--porcelain"), "")
        self.assertEqual(self.settings(root)["version"], "v1.1.0")

    def test_commits_made_but_not_pushed_are_undone(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        self.assertPasses(self.roll("v1.1.0", "--apply"))  # one unpushed commit
        self.assertNotEqual(self.head(root), head)
        self.write_record(root, head)
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("RESTORED       alpha", proc.stdout)
        self.assertIn("UPDATED        alpha", proc.stdout)
        self.assertEqual(git(root, "rev-list", "--count", head + "..HEAD"), "1")
        self.assertFalse(os.path.exists(self.record_path(root)))

    def test_an_edit_to_a_file_the_record_does_not_name_is_blocked(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        self.write_record(root, head)
        write(os.path.join(root, "AGENTS.md"), "# my own edit\n")
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("BLOCKED        alpha: an earlier run stopped here and the repository changed since; check it by hand, then delete .git/%s" % RECORD, proc.stdout)
        self.assertEqual(read(os.path.join(root, "AGENTS.md")), "# my own edit\n")
        self.assertEqual(self.head(root), head)
        self.assertTrue(os.path.exists(self.record_path(root)))

    def test_an_untracked_file_is_blocked(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        self.write_record(root, head)
        write(os.path.join(root, "notes.txt"), "mine\n")
        self.assertBlocks(self.roll("v1.1.0", "--apply"), None)
        self.assertTrue(os.path.exists(os.path.join(root, "notes.txt")))
        self.assertTrue(os.path.exists(self.record_path(root)))

    def test_commits_that_reached_origin_are_blocked(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        self.assertPasses(self.roll("v1.1.0", "--apply", "--push"))
        pushed = self.head(root)
        self.write_record(root, head)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("the repository changed since", proc.stdout)
        self.assertEqual(self.head(root), pushed)
        self.assertTrue(os.path.exists(self.record_path(root)))

    def test_a_commit_with_another_subject_is_blocked(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        write(os.path.join(root, "AGENTS.md"), "# more\n")
        git(root, "add", "AGENTS.md")
        git(root, "commit", "-q", "-m", "docs: someone's own work")
        mine = self.head(root)
        self.write_record(root, head)
        self.assertBlocks(self.roll("v1.1.0", "--apply"))
        self.assertEqual(self.head(root), mine)

    def test_another_branch_is_blocked(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        git(root, "checkout", "-q", "-b", "topic")
        self.write_record(root, head)
        self.assertBlocks(self.roll("v1.1.0", "--apply"))
        self.assertEqual(git(root, "symbolic-ref", "--short", "HEAD"), "topic")

    def test_a_record_held_by_a_live_process_is_another_roll_out(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        self.write_record(root, head, pid=4711)
        with open(self.record_path(root), "r+") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for flags in ((), ("--apply",)):
                proc = self.roll("v1.1.0", *flags)
                self.assertBlocks(proc)
                self.assertIn("BLOCKED        alpha: another roll-out (pid 4711) is working here", proc.stdout)
        self.assertEqual(self.head(root), head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")

    def test_an_unreadable_record_is_blocked(self):
        root = self.make_consumer("alpha")
        write(self.record_path(root), "{not json")
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("cannot be read; check it by hand, then delete .git/%s" % RECORD, proc.stdout)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")

    def test_a_record_in_a_repository_that_does_not_name_the_package_is_still_restored(self):
        root = self.make_consumer("style", vale="v0.3.4", checks=False, packages=["house-style"])
        head = self.head(root)
        self.write_record(root, head, files=[".vale.ini"], subjects=["ci: load the house style at v0.3.6"])
        write(os.path.join(root, ".vale.ini"), "StylesPath = other\n")
        git(root, "add", ".vale.ini")
        proc = self.roll("v1.1.0", "--apply")  # moves commit-checks only; this repository names house-style
        self.assertPasses(proc)
        self.assertIn("RESTORED       style", proc.stdout)
        self.assertEqual(git(root, "status", "--porcelain"), "")
        self.assertEqual(self.head(root), head)
        self.assertFalse(os.path.exists(self.record_path(root)))

    def test_a_repository_without_a_record_is_unaffected(self):
        root = self.make_consumer("alpha", verify=VERIFY_FAIL)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertNotIn("RESTORED", proc.stdout)
        self.assertFalse(os.path.exists(self.record_path(root)))  # a failed verification removes its record too

    def test_a_pushed_pull_request_branch_is_restored_with_a_note(self):
        agents = "# Agents\n\nCollaborative: use branches and pull requests.\n"
        root = self.make_consumer("alpha", agents=agents)
        head = self.head(root)
        git(root, "checkout", "-q", "-b", "ci/shared-pins")
        write(os.path.join(root, ".commit-checks.json"), read(os.path.join(root, ".commit-checks.json")).replace("v1.0.0", "v1.1.0"))
        git(root, "add", ".commit-checks.json")
        git(root, "commit", "-q", "-m", SUBJECT)
        git(root, "push", "-q", "origin", "ci/shared-pins")
        git(root, "checkout", "-q", "main")
        self.write_record(root, head, collab=True)
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("RESTORED       alpha", proc.stdout)
        self.assertIn("origin/ci/shared-pins exists; roll-out never deletes a remote branch", proc.stdout)
        self.assertEqual(git(root, "ls-remote", "origin", "refs/heads/ci/shared-pins").split()[0] != "", True)  # still there


class TestSigkillDuringCommit(RecoveryBase):
    CHECK = ""

    def test_a_killed_roll_out_leaves_no_commit_hook_child(self):
        root = self.make_consumer("alpha", hook=HOOK_BG_HANG)
        proc = self.roll_started("v1.1.0", "--apply")
        pid = self.bg_pid(root)
        self.addCleanup(lambda: pid_alive(pid) and os.kill(pid, signal.SIGKILL))
        proc.kill()
        proc.communicate()
        wait_until(self, lambda: not pid_alive(pid), "the hook's background child to be gone")
