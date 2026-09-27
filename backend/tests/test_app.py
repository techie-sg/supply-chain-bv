from app import app


def test_hello_world() -> None:
    with app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "Hello, World!"
