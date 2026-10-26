"""Download public model weights once, then serve documents locally on CPU."""
from pathlib import Path
import os
import subprocess
import sys

artifacts = Path(os.environ.get("DOCLING_ARTIFACTS_PATH", "/models/artifacts"))
ready = artifacts / ".viio-ready-2.142.1"
if not ready.exists():
    subprocess.run(["docling-tools", "models", "download", "layout", "tableformer", "--output-dir", str(artifacts)], check=True)
    ready.touch()
os.execvp("uvicorn", ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"])
