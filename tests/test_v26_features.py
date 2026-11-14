import os,sys,tempfile,subprocess
from pathlib import Path

def test_v26_flow():
    script=r'''
import os,sys,tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from io import BytesIO
from openpyxl import load_workbook
f=tempfile.NamedTemporaryFile(suffix='.db',delete=False);f.close()
os.environ['DATABASE_URL']='sqlite:///'+f.name
os.environ['COOKIE_SECURE']='false'
os.environ['ADMIN_EMAIL']='admin@kr.test';os.environ['ADMIN_PASSWORD']='supersecretpassword1234'
sys.path.insert(0,'.')
from app.main import app
with TestClient(app,headers={'Origin':'http://testserver'}) as c:
  assert c.post('/api/register',json={'email':'client@kr.test','password':'secretpassword123'}).status_code==200
  assert c.post('/api/login',json={'email':'client@kr.test','password':'secretpassword123'}).status_code==200
  assert c.get('/api/pro/profitability').status_code==403
  assert c.post('/api/pro/selling-price',json={'recipe_id':1,'mode':'margin','target_pct':'35'}).status_code==403
  c.post('/api/logout',json={})
  assert c.post('/api/login',json={'email':'admin@kr.test','password':'supersecretpassword1234'}).status_code==200
  assert c.put('/api/admin/users/2/plan',json={'plan':'pro','duration_days':365}).status_code==200
  c.post('/api/logout',json={});assert c.post('/api/login',json={'email':'client@kr.test','password':'secretpassword123'}).status_code==200
  r=c.post('/api/ingredients',json={'name':'Tepung','purchase_qty':'1','purchase_unit':'kg','usage_unit':'g','price_per_unit':'14000'});assert r.status_code==200,r.text
  a=c.post('/api/recipes',json={'name':'Adonan','yield_qty':'10','items':[{'ingredient_id':1,'qty':'500','unit':'g'}]});assert a.status_code==200,a.text
  aid=a.json()['id']
  b=c.post('/api/recipes',json={'name':'Roti','yield_qty':'5','items':[],'components':[{'recipe_id':aid,'qty':'5'}]});assert b.status_code==200,b.text
  bid=b.json()['id'];v=c.get(f'/api/recipes/{bid}/calculate?target=5');assert v.status_code==200,v.text
  assert float(v.json()['total_cost'])==3500,v.json()
  cyc=c.put(f'/api/recipes/{aid}',json={'name':'Adonan','yield_qty':'10','items':[],'components':[{'recipe_id':bid,'qty':'5'}]});assert cyc.status_code==200,cyc.text
  assert c.get(f'/api/recipes/{bid}/calculate?target=5').status_code==422
  c.put(f'/api/recipes/{aid}',json={'name':'Adonan','yield_qty':'10','items':[{'ingredient_id':1,'qty':'500','unit':'g'}]})
  r=c.post('/api/pro/scenario',json={'recipe_id':bid,'changes':{'1':'20'}});assert r.status_code==200,r.text
  assert float(r.json()['after']['total_cost'])==4200,r.json()
  assert float(c.get(f'/api/recipes/{bid}/calculate?target=5').json()['total_cost'])==3500
  r=c.post('/api/pro/selling-price',json={'recipe_id':bid,'mode':'margin','target_pct':'30','rounding':500,'save':True});assert r.status_code==200,r.text
  assert float(r.json()['suggested_price'])==1000,r.json()
  rows=c.get('/api/pro/profitability');assert rows.status_code==200,rows.text
  assert any(x['recipe_id']==bid and x['food_cost_pct'] is not None for x in rows.json())
  t=c.get('/api/pro/import/template');assert t.status_code==200,t.text[:300]
  wb=load_workbook(BytesIO(t.content));ws=wb['Bahan'];ws.delete_rows(2);ws.append(['Gula',1,'kg','g',18000]);rs=wb['Resep'];rs.delete_rows(2);rs.append(['R002','Sirup',10,'pcs','Gula',100,'g',0,0,0]);out=BytesIO();wb.save(out)
  prev=c.post('/api/pro/import/preview',files={'file':('import.xlsx',out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')});assert prev.status_code==200,prev.text
  assert prev.json()['ingredients']==1 and prev.json()['recipes']==1
  commit=c.post('/api/pro/import/commit',json={'token':prev.json()['token']});assert commit.status_code==200,commit.text
  assert len(c.get('/api/ingredients').json())==2
  assert len(c.get('/api/recipes').json())==3
  print('V26 FLOW OK')
'''
    r=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=45)
    assert r.returncode==0,r.stdout+'\n'+r.stderr
