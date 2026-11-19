"""Launcher used by the loader: runs the package that sits next to this file."""
import sys

from commit_checks.cli import main

sys.exit(main())
