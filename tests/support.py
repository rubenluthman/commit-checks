"""Test helpers. Fake secrets are built from pieces here, so no key-shaped text sits in a file."""
import json
import os
import random
import shutil
import stat
import string
import subprocess
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(PKG, "tests", "canary_shim")
GITLEAKS = shutil.which("gitleaks")

_rng = random.Random(20261119)


def rnd(n, alphabet=string.ascii_letters + string.digits):
    return "".join(_rng.choice(alphabet) for _ in range(n))


def _pk(kind):
    return "-----BEGIN " + kind + "PRIV" + "ATE KEY-----\n" + rnd(64) + "\n-----END " + kind + "PRIV" + "ATE KEY-----\n"


def fake_keys():
    """name -> a fake secret of that shape."""
    return {
        "openai-proj": "sk-" + "proj-" + rnd(60),
        "openai-proj-dashes": "sk-" + "proj-" + rnd(20) + "_" + rnd(20) + "-" + rnd(12),
        "anthropic": "sk-" + "ant-" + "api03-" + rnd(40) + "_" + rnd(30),
        "github-pat": "github" + "_pat_" + rnd(22) + "_" + rnd(40),
        "github-token": "gh" + "p_" + rnd(36),
        "github-oauth": "gh" + "o_" + rnd(36),
        "aws": "AK" + "IA" + rnd(16, string.ascii_uppercase + string.digits),
        "stripe-live": "sk_" + "live_" + rnd(24),
        "google": "AI" + "za" + rnd(35, string.ascii_letters + string.digits + "_-"),
        "slack": "xo" + "xb-" + rnd(12, string.digits) + "-" + rnd(24),
        "npm": "np" + "m_" + rnd(36),
        "low-entropy-openai": "sk-" + "proj-" + "A" * 40,
        "key-rsa": _pk("RSA "),
        "key-pkcs8": _pk(""),
        "key-encrypted": _pk("ENCRYPTED "),
        "key-openssh": _pk("OPENSSH "),
        "key-ec": _pk("EC "),
        "key-header-only": "-----BEGIN " + "PRIV" + "ATE KEY-----\n",
    }


def home_path(name="bob"):
    return "/Use" + "rs/" + name + "/project/file.txt"


def email(addr="bob", dom="gmail.com"):
    return addr + "@" + dom


def conflict_marker(c="<"):
    return c * 7 + " HEAD\n"


def env_for(extra=None, canary=False, names_file=None, path=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GITLEAKS", "COMMIT_CHECKS", "CI"))}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["PYTHONPATH"] = PKG + (os.pathsep + SHIM if canary else "")
    env["COMMIT_CHECKS_PRIVATE_NAMES_FILE"] = names_file or os.path.join(tempfile.gettempdir(), "commit-checks-no-such-list")
    if canary:
        env["COMMIT_CHECKS_CANARY"] = "1"
    if path is not None:
        env["PATH"] = path
    env.update(extra or {})
    return env


class Repo:
    def __init__(self, visibility="private", settings_extra=None, commit_first=False, email_addr="1+t@users.noreply.github.com"):
        self.dir = tempfile.mkdtemp(prefix="cc-test-")
        self.root = os.path.realpath(self.dir)
        self.names_file = os.path.join(self.root + "-names")
        with open(self.names_file, "w") as fh:
            fh.write("# test names\nzebra" + "foo\nquux" + "bar\n")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", email_addr)
        self.git("config", "user.name", "Test")
        self.git("config", "core.hooksPath", "/dev/null")
        data = {"visibility": visibility, "self": True}  # "self" skips the loader check; loader tests turn it off
        data.update(settings_extra or {})
        self.write(".commit-checks.json", json.dumps(data))
        self.write(".gitignore", "\n".join([".env", ".env.*", ".dev.vars*", "*.p8", "*.p12", "*.key", "*.pem", "*.mobileprovision", "id_*", ""]))
        self.git("add", ".commit-checks.json", ".gitignore")
        if commit_first:
            self.git("commit", "-q", "-m", "chore: start")

    def path(self, rel):
        return os.path.join(self.root, rel)

    def write(self, rel, text, binary=False):
        full = self.path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb" if binary else "w", encoding=None if binary else "utf-8") as fh:
            fh.write(text)

    def git(self, *args, check=True, env=None):
        proc = subprocess.run(["git"] + list(args), cwd=self.root, capture_output=True, text=True,
                              env=env_for(extra=env), errors="replace")
        if check and proc.returncode != 0:
            raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr))
        return proc

    def stage(self, rel, text):
        self.write(rel, text)
        self.git("add", "--", rel)

    def commit(self, msg="chore: change"):
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def settings(self, updates=None, **kw):
        with open(self.path(".commit-checks.json")) as fh:
            data = json.load(fh)
        data.update(updates or {})
        data.update(kw)
        self.write(".commit-checks.json", json.dumps(data))
        self.git("add", ".commit-checks.json")

    def run(self, mode, *args, canary=False, use_names=False, env=None, path=None, stdin=None, cwd=None, timeout=300):
        merged = {"CI": "1"}  # the hooks check is local only; its own tests pass CI=""
        merged.update(env or {})
        e = env_for(extra=merged, canary=canary, names_file=self.names_file if use_names else None, path=path)
        return subprocess.run([sys.executable, "-m", "commit_checks", mode] + list(args), cwd=cwd or self.root,
                              capture_output=True, text=True, env=e, input=stdin, timeout=timeout, errors="replace")

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        try:
            os.remove(self.names_file)
        except OSError:
            pass


def fake_gitleaks_dir(script_body):
    """A folder holding a fake gitleaks (a shell script) plus git and python, for failure-mode tests."""
    d = tempfile.mkdtemp(prefix="cc-fakebin-")
    for tool in ("git", "python3"):
        real = shutil.which(tool)
        os.symlink(real, os.path.join(d, tool))
    os.symlink(sys.executable, os.path.join(d, "python"))
    if script_body is not None:
        p = os.path.join(d, "gitleaks")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\n" + script_body + "\n")
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    return d


def path_with(fake_dir):
    """PATH holding only the fake folder and the system tools sh/sleep need."""
    return fake_dir + os.pathsep + "/bin" + os.pathsep + "/usr/bin"


class Base(unittest.TestCase):
    CHECK = ""

    def setUp(self):
        if not GITLEAKS:
            self.fail("gitleaks is not installed; the suite cannot run")
        self.repos = []

    def repo(self, *a, **kw):
        r = Repo(*a, **kw)
        self.repos.append(r)
        return r

    def tearDown(self):
        for r in self.repos:
            r.cleanup()

    def assertBlocks(self, proc, text=None):
        self.assertEqual(proc.returncode, 1, "expected a finding; got rc=%d\n%s%s" % (proc.returncode, proc.stdout, proc.stderr))
        if text:
            self.assertIn(text, proc.stderr)

    def assertPasses(self, proc):
        self.assertEqual(proc.returncode, 0, "expected a pass; got rc=%d\n%s%s" % (proc.returncode, proc.stdout, proc.stderr))

    def assertErrors(self, proc, text=None):
        self.assertEqual(proc.returncode, 2, "expected an error; got rc=%d\n%s%s" % (proc.returncode, proc.stdout, proc.stderr))
        if text:
            self.assertIn(text, proc.stderr)
