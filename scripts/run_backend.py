"""Start FastAPI, subscribers and backend services.

Owner: Jerome & Richard
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run from a checkout without an installation step: the repository root holds `backend`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from backend.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "backend.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
