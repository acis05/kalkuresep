import os,tempfile,sys
from pathlib import Path
from fastapi.testclient import TestClient
f=tempfile.NamedTemporaryFile(suffix='.db',delete=False);f.close();os.environ['DATABASE_URL']='sqlite:///'+f.name;os.environ['COOKIE_SECURE']='false';os.environ['ADMIN_EMAIL']='admin@example.com';os.environ['ADMIN_PASSWORD']='supersecretpassword1234';sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import app
with TestClient(app,headers={'Origin':'http://testserver'}) as client:
  assert client.get('/health').status_code==200
  assert client.post('/api/register',json={'email':'user@example.com','password':'password123456'}).status_code==200
  assert client.post('/api/login',json={'email':'user@example.com','password':'password123456'}).status_code==200
  assert client.post('/api/ingredients',json={'name':'Tepung','purchase_qty':'1','purchase_unit':'g','usage_unit':'g','price_per_unit':'12'}).status_code==200
  r=client.post('/api/recipes',json={'name':'Roti','yield_qty':'20','items':[{'ingredient_id':1,'qty':'1000'}],'packaging_cost':'2000','labor_cost':'3000','overhead_cost':'1000'});assert r.status_code==200,r.text
  rid=r.json()['id'];r=client.get(f'/api/recipes/{rid}/calculate?target=50&sell_price=5000');assert r.status_code==200,r.text
  assert r.json()['total_cost']=='45000.0',r.json()
  assert r.json()['hpp_per_unit']=='900.0',r.json()
  # Activate Pro as admin before testing Pro-only Excel export.
  client.post('/api/logout',json={})
  assert client.post('/api/login',json={'email':'admin@example.com','password':'supersecretpassword1234'}).status_code==200
  r=client.put('/api/admin/users/2/plan',json={'plan':'pro','duration_days':365});assert r.status_code==200,r.text
  client.post('/api/logout',json={})
  assert client.post('/api/login',json={'email':'user@example.com','password':'password123456'}).status_code==200
  for ext in ['pdf','xlsx']:
    r=client.get(f'/api/recipes/{rid}/export.{ext}?target=50');assert r.status_code==200,(ext,r.text[:300]);print(ext,len(r.content))
  client.post('/api/logout',json={})
  assert client.post('/api/login',json={'email':'admin@example.com','password':'supersecretpassword1234'}).status_code==200
  r=client.put('/api/admin/users/2/plan',json={'plan':'pro','duration_days':365});assert r.status_code==200,r.text;assert r.json()['pro_expires_at']
  print('SMOKE TEST PASS')
