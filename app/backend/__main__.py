"""`python -m backend` - start the API server using values from `.env`
(VAHAN_HOST / VAHAN_PORT / VAHAN_RELOAD). Equivalent to running uvicorn by hand.
"""
from __future__ import annotations

import uvicorn

from backend.settings import settings


def main() -> None:
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
