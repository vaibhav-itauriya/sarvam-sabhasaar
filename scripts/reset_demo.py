"""Re-create the sample meeting (for example after make_demo_audio). Run: python -m scripts.reset_demo"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.demo import seed_demo  # noqa: E402

seed_demo(force=True)
print("Sample meeting reset.")
