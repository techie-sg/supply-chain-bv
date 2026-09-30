import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"

sys.path.insert(0, str(SRC_DIR))

from dispatchdesk.app.gradio_app import app


if __name__ == "__main__":
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
    )