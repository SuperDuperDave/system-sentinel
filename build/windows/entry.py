"""The exe's entry point: nothing but the launcher, so PyInstaller has one thing to start."""

import sys

from sentinel.launcher import main

if __name__ == "__main__":
    sys.exit(main())
