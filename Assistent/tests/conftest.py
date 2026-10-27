"""Cross-service contract tests use the standalone consultant source checkout."""
import sys
from pathlib import Path

source = Path(__file__).resolve().parents[2] / 'Consultant' / 'src'
if source.is_dir():
    sys.path.insert(0, str(source))
