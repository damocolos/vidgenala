"""Shared test config: the test clients use Host 'testserver' (TestClient) or 'test'
(httpx AsyncClient(base_url="http://test")), which the Host guard would otherwise reject."""
import os

os.environ.setdefault("NALARKA_ALLOWED_HOSTS", "testserver,test")
