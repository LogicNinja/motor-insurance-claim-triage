import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for sub in ("", "services/api", "services/worker", "services/verifier", "services/retrieval"):
    sys.path.insert(0, str(ROOT / sub) if sub else str(ROOT))
os.environ.setdefault("SCHEMA_DIR", str(ROOT / "schemas"))
