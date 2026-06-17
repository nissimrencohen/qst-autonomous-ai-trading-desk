"""Puts the service root on sys.path so tests import `app.*`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
