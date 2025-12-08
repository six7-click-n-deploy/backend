import sys
import os
import pytest
from fastapi.testclient import TestClient

# /app ins PYTHONPATH aufnehmen
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from main import app


@pytest.fixture
def client():
    return TestClient(app)
