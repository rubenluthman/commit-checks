"""The leak check in support.Base fails a test that leaves a file or socket open."""
import socket
import unittest

import support
from support import Base


def run_inner(body):
    """Run one Base test with its own result; return that result."""
    class Inner(Base):
        def test_it(self):
            body()

    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(Inner).run(result)
    return result


class LeakCheckTests(Base):
    CHECK = ""  # guards the suite; it is not a commit check, so the canary has no entry for it

    def run_inner(self, body):
        before = len(support._leaks)
        self.addCleanup(self._drop_inner_leaks, before)
        # Catch what reaches the previous hook, so the inner leak does not print into the outer run.
        self.forwarded = []
        original = support._previous_hook
        support._previous_hook = self.forwarded.append
        self.addCleanup(setattr, support, "_previous_hook", original)
        return run_inner(body)

    def _drop_inner_leaks(self, before):
        # The inner leak lands in the same record; drop it, or the outer test would fail itself.
        del support._leaks[before:]

    def test_a_leaked_file_fails_the_test(self):
        def leak():
            f = open(__file__)
            del f
        result = self.run_inner(leak)
        self.assertEqual(len(result.failures), 1, result.errors)
        self.assertIn("left open", result.failures[0][1])
        self.assertIn("unclosed file", result.failures[0][1])

    def test_a_leaked_socket_fails_the_test(self):
        def leak():
            s = socket.socket()
            del s
        result = self.run_inner(leak)
        self.assertEqual(len(result.failures), 1, result.errors)
        self.assertIn("unclosed", result.failures[0][1])
        self.assertIn("socket", result.failures[0][1])

    def test_a_recorded_leak_also_reaches_the_previous_hook(self):
        def leak():
            f = open(__file__)
            del f
        self.run_inner(leak)
        self.assertEqual(len(self.forwarded), 1)
        self.assertIs(self.forwarded[0].exc_type, ResourceWarning)

    def test_a_closed_file_passes(self):
        def tidy():
            with open(__file__) as f:
                f.read()
        result = self.run_inner(tidy)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])
        self.assertEqual(result.testsRun, 1)


if __name__ == "__main__":
    unittest.main()
