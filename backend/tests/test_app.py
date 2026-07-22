import pytest

# The `app`/`client` fixtures always provision a real db_session override
# (see conftest.py), even for tests like these that never touch the
# database - so they require the disposable test database like every other
# fixture consumer, regardless of what each individual test asserts.
pytestmark = pytest.mark.integration


def test_create_app_succeeds(app):
    assert app.title == "Clinic Admin Platform API"


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
