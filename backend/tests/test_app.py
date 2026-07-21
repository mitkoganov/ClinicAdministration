def test_create_app_succeeds(app):
    assert app.title == "Clinic Admin Platform API"


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
