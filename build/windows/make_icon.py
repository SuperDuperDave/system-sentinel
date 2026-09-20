"""Write the exe's icon from the same mark the tray draws, so there is one rendering, not two."""

from pathlib import Path

from sentinel.launcher import write_icon

if __name__ == "__main__":
    print(write_icon(Path(__file__).with_name("SystemSentinel.ico")))
