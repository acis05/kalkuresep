"""Regresi kuota paket Free v2.10."""
from fastapi.testclient import TestClient
from app.main import app

def test_free_limits():
    with TestClient(app, base_url="https://testserver", headers={"Origin":"https://testserver"}) as client:
        import uuid
        email = f"quota-{uuid.uuid4().hex[:12]}@example.com"
        password = "TestPassword12345!"
        assert client.post('/api/register', json={'email': email, 'password': password}).status_code == 200
        assert client.post('/api/login', json={'email': email, 'password': password}).status_code == 200
        limits = client.get('/api/me').json()['limits']
        assert limits['ingredients'] == 20
        assert limits['recipes'] == 5
        for i in range(20):
            r = client.post('/api/ingredients', json={'name': f'Bahan {i}', 'purchase_qty': '1', 'purchase_unit': 'g', 'usage_unit': 'g', 'price_per_unit': '100'})
            assert r.status_code == 200, r.text
        r = client.post('/api/ingredients', json={'name': 'Bahan 21', 'purchase_qty': '1', 'purchase_unit': 'g', 'usage_unit': 'g', 'price_per_unit': '100'})
        assert r.status_code == 403 and '20 bahan' in r.text
