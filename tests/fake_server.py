"""Run the UI against the fake Sarvam service:  python -m tests.fake_server  (port 8078)"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="sabhasaar-fake-"))
import uvicorn  # noqa: E402

from app import config, main  # noqa: E402
from tests.fake_sarvam import FakeSarvam  # noqa: E402

_fake = FakeSarvam()
main.get_service = lambda: _fake
config.has_key = lambda: True

if __name__ == "__main__":
    uvicorn.run(main.app, port=8078)
