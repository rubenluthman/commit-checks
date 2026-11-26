import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from support import PKG, Base, env_for


def make_source(tag="vtest"):
    """A git repository holding the package, with an annotated tag. Returns (path, commit)."""
    src = tempfile.mkdtemp(prefix="cc-src-")
    for name in ("commit_checks", "templates", "run_checks.py"):
        full = os.path.join(PKG, name)
        if os.path.isdir(full):
            shutil.copytree(full, os.path.join(src, name), ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy(full, src)
    env = env_for()
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=src, check=True, capture_output=True, text=True, env=env).stdout.strip()
    run("init", "-q", "-b", "main")
    run("config", "user.email", "1+t@users.noreply.github.com")
    run("config", "user.name", "T")
    run("add", "-A")
    run("commit", "-q", "-m", "release")
    run("tag", "-a", tag, "-m", "release")
    return src, run("rev-parse", tag + "^{commit}")


class LoaderBase(Base):

    def setUp(self):
        super().setUp()
        self.src, self.commit = make_source()
        self.addCleanup(shutil.rmtree, self.src, True)

    def adopt(self, r, commit=None, version="vtest", source=None):
        shutil.copy(os.path.join(PKG, "templates", "loader.py"), r.path("scripts_loader_tmp"))
        os.makedirs(r.path("scripts"), exist_ok=True)
        os.replace(r.path("scripts_loader_tmp"), r.path("scripts/commit-checks"))
        os.chmod(r.path("scripts/commit-checks"), 0o755)
        extra = {"version": version, "commit": commit or self.commit, "self": False}
        if source:
            extra["source"] = source
        r.settings(extra)
        r.git("add", "scripts/commit-checks")

    def load(self, r, *args, local=True, env=None):
        e = env_for(extra=dict({"CI": "1"}, **(env or {})))
        if local:
            e["COMMIT_CHECKS_PATH"] = self.src
        return subprocess.run([sys.executable, r.path("scripts/commit-checks")] + list(args or ["verify"]), cwd=r.root,
                              capture_output=True, text=True, env=e, timeout=300)

class LoaderTests(LoaderBase):
    CHECK = "loader"

    def test_loads_the_pinned_release_from_the_local_folder_and_runs_it(self):
        r = self.repo()
        self.adopt(r)
        self.assertPasses(self.load(r))

    def test_the_loaded_package_blocks_a_secret(self):
        from support import fake_keys
        r = self.repo()
        self.adopt(r)
        r.stage("a.txt", "k = %s\n" % fake_keys()["github-token"])
        self.assertBlocks(self.load(r), "FAIL secrets")

    def test_a_wrong_commit_id_fails(self):
        r = self.repo()
        self.adopt(r, commit="1" * 40)
        self.assertErrors(self.load(r), "pin")

    def test_a_missing_release_tag_fails(self):
        r = self.repo()
        self.adopt(r, version="v9.9.9")
        self.assertErrors(self.load(r), "not found")

    def test_a_moved_tag_fails(self):
        r = self.repo()
        self.adopt(r)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "later"], cwd=self.src, check=True, env=env_for())
        subprocess.run(["git", "tag", "-f", "-a", "vtest", "-m", "moved"], cwd=self.src, check=True, capture_output=True, env=env_for())
        self.assertErrors(self.load(r), "pin")

    def test_a_short_commit_id_fails(self):
        r = self.repo()
        self.adopt(r, commit=self.commit[:10])
        self.assertErrors(self.load(r), "40-character")

    def test_no_local_folder_and_no_source_fails(self):
        r = self.repo()
        self.adopt(r)
        self.assertErrors(self.load(r, local=False), "no local package folder")

    def test_fetches_from_the_source_when_there_is_no_local_folder(self):
        r = self.repo()
        self.adopt(r, source=self.src)
        self.assertPasses(self.load(r, local=False))

    def test_a_source_with_a_different_commit_fails(self):
        r = self.repo()
        other, _c = make_source()
        self.addCleanup(shutil.rmtree, other, True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "x"], cwd=other, check=True, env=env_for())
        subprocess.run(["git", "tag", "-f", "-a", "vtest", "-m", "x"], cwd=other, check=True, capture_output=True, env=env_for())
        self.adopt(r, source=other)
        self.assertErrors(self.load(r, local=False), "pin")

    def test_an_unreachable_source_fails(self):
        r = self.repo()
        self.adopt(r, source="/nonexistent/place")
        self.assertErrors(self.load(r, local=False), "cannot fetch")

    def test_the_cache_serves_later_runs_without_the_source(self):
        r = self.repo()
        self.adopt(r)
        self.assertPasses(self.load(r))
        shutil.rmtree(self.src)
        self.assertPasses(self.load(r, local=False))

    def test_a_tampered_cache_marker_is_refetched(self):
        r = self.repo()
        self.adopt(r)
        self.assertPasses(self.load(r))
        marker = os.path.join(r.root, ".git", "commit-checks", self.commit, ".commit")
        with open(marker, "w") as fh:
            fh.write("2" * 40 + "\n")
        self.assertPasses(self.load(r))
        with open(marker) as fh:
            self.assertEqual(fh.read().strip(), self.commit)

    def test_works_in_a_worktree(self):
        r = self.repo(commit_first=True)
        self.adopt(r)
        r.git("add", "-A")
        r.commit("chore: adopt")
        wt = r.root + "-wt"
        r.git("worktree", "add", "-q", wt, "-b", "topic")
        self.addCleanup(shutil.rmtree, wt, True)
        e = env_for(extra={"CI": "1", "COMMIT_CHECKS_PATH": self.src})
        proc = subprocess.run([sys.executable, os.path.join(wt, "scripts/commit-checks"), "verify"], cwd=wt, capture_output=True, text=True, env=e)
        self.assertPasses(proc)


class LoaderCurrentTests(LoaderBase):
    CHECK = "loader-current"

    def test_an_edited_loader_fails_verify(self):
        r = self.repo()
        self.adopt(r)
        with open(r.path("scripts/commit-checks"), "a") as fh:
            fh.write("# sneaky\n")
        self.assertBlocks(self.load(r), "loader-current")

    def test_a_missing_loader_fails_verify(self):
        r = self.repo()
        r.settings({"self": False})
        proc = r.run("verify")
        self.assertBlocks(proc, "loader-current")


class _Handler(http.server.BaseHTTPRequestHandler):
    answer = b'{"private": false}'
    code = 200

    def do_GET(self):
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(self.answer)

    def log_message(self, *args):
        pass


class VisibilityTests(Base):
    CHECK = "visibility"

    def serve(self, answer, code=200):
        handler = type("H", (_Handler,), {"answer": answer, "code": code})
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # Cleanups run last in, first out: stop the loop, wait for the thread, close the socket.
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_port

    def ci(self, r, url, **extra):
        env = {"GITHUB_REPOSITORY": "owner/repo", "COMMIT_CHECKS_API_URL": url, "COMMIT_CHECKS_PRIVATE_NAMES": "zebra" + "foo"}
        env.update(extra)
        return r.run("ci", env=env)

    def adopt(self, r):
        shutil.copy(os.path.join(PKG, "templates", "loader.py"), r.path("loader.tmp"))
        os.makedirs(r.path("scripts"), exist_ok=True)
        os.replace(r.path("loader.tmp"), r.path("scripts/commit-checks"))
        r.settings({"self": False})
        r.git("add", "scripts/commit-checks")

    def test_public_on_github_but_private_in_settings_fails(self):
        r = self.repo(visibility="private")
        self.adopt(r)
        self.assertBlocks(self.ci(r, self.serve(b'{"private": false}')), "visibility")

    def test_matching_public_passes(self):
        r = self.repo(visibility="public")
        self.adopt(r)
        self.assertPasses(self.ci(r, self.serve(b'{"private": false}')))

    def test_going_public_while_still_private_passes(self):
        r = self.repo(visibility="going-public")
        self.adopt(r)
        self.assertPasses(self.ci(r, self.serve(b'{"private": true}')))

    def test_private_on_github_but_public_in_settings_fails(self):
        r = self.repo(visibility="public")
        self.adopt(r)
        self.assertBlocks(self.ci(r, self.serve(b'{"private": true}')), "visibility")

    def test_an_unreachable_or_broken_answer_fails(self):
        r = self.repo(visibility="public")
        self.adopt(r)
        self.assertErrors(self.ci(r, "http://127.0.0.1:9"), "cannot ask GitHub")
        self.assertErrors(self.ci(r, self.serve(b"not json")), "cannot ask GitHub")
        self.assertErrors(self.ci(r, self.serve(b'{"private": "yes"}')), "unexpected")
        self.assertErrors(self.ci(r, self.serve(b"{}", code=500)), "cannot ask GitHub")


class HookRootTests(LoaderBase):
    """The loader runs inside real hooks, in a main checkout and in a linked worktree."""

    def hooked(self, r):
        self.adopt(r)
        shutil.copytree(os.path.join(PKG, "templates", "githooks"), r.path(".githooks"))
        for name in os.listdir(r.path(".githooks")):
            os.chmod(os.path.join(r.path(".githooks"), name), 0o755)
        r.git("config", "core.hooksPath", ".githooks")
        r.git("add", ".githooks")
        r.git("commit", "-q", "-m", "chore: start", env={"COMMIT_CHECKS_PATH": self.src, "CI": "1"})

    def commit_in(self, path, rel):
        with open(os.path.join(path, rel), "w") as fh:
            fh.write("hello\n")
        env = env_for(extra={"COMMIT_CHECKS_PATH": self.src, "CI": "1"})
        run = lambda *a: subprocess.run(["git"] + list(a), cwd=path, capture_output=True, text=True, env=env)
        run("add", rel)
        return run("commit", "-q", "-m", "chore: add " + rel)

    def test_a_commit_through_the_hooks_passes_in_a_main_checkout(self):
        r = self.repo()
        self.hooked(r)
        proc = self.commit_in(r.root, "a.txt")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_commit_through_the_hooks_passes_in_a_linked_worktree(self):
        r = self.repo()
        self.hooked(r)
        wt = os.path.join(tempfile.mkdtemp(prefix="cc-wt-"), "wt")
        self.addCleanup(shutil.rmtree, os.path.dirname(wt), True)
        r.git("worktree", "add", "-q", "-b", "side", wt)
        proc = self.commit_in(wt, "b.txt")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("cannot read version", proc.stdout + proc.stderr)

    def test_a_loader_outside_root_scripts_fails(self):
        r = self.repo()
        self.adopt(r)
        os.makedirs(r.path("tools"))
        shutil.copy(r.path("scripts/commit-checks"), r.path("tools/commit-checks"))
        e = env_for(extra={"CI": "1", "COMMIT_CHECKS_PATH": self.src})
        proc = subprocess.run([sys.executable, r.path("tools/commit-checks"), "verify"], cwd=r.root,
                              capture_output=True, text=True, env=e)
        self.assertErrors(proc, "must live at")
