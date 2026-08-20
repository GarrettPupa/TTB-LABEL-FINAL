"""Local configuration bootstrap.

Production deployments should inject environment variables directly. For local
development only, ``.env`` is loaded without overriding an already-set variable.
"""

from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_local_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

