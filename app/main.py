import os, json, secrets, hashlib, hmac, time
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, ForeignKey, Text, select, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker

DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./kalkuresep_dev.db')
if DATABASE_URL.startswith('postgres://'): DATABASE_URL='postgresql+psycopg://'+DATABASE_URL[len('postgres://'):]
elif DATABASE_URL.startswith('postgresql://'): DATABASE_URL='postgresql+psycopg://'+DATABASE_URL[len('postgresql://'):]
engine=create_engine(DATABASE_URL, connect_args={'check_same_thread':False} if DATABASE_URL.startswith('sqlite') else {},pool_pre_ping=True)
SessionLocal=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    email:Mapped[str]=mapped_column(String(254),unique=True,index=True)
    password_hash:Mapped[str]=mapped_column(Text)
    plan:Mapped[str]=mapped_column(String(10),default='free')
    is_admin:Mapped[bool]=mapped_column(default=False)
    active:Mapped[bool]=mapped_column(default=True)
    pro_expires_at:Mapped[int|None]=mapped_column(Integer,nullable=True)
class LoginSession(Base):
    __tablename__='login_sessions'
    id:Mapped[int]=mapped_column(primary_key=True)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    token_hash:Mapped[str]=mapped_column(String(64),unique=True)
    expires:Mapped[int]=mapped_column(Integer)
class Ingredient(Base):
    __tablename__='ingredients'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    name:Mapped[str]=mapped_column(String(120))
    unit:Mapped[str]=mapped_column(String(20))
    price_per_unit:Mapped[str]=mapped_column(String(40))
    purchase_qty:Mapped[str]=mapped_column(String(40),default='1')
    purchase_unit:Mapped[str]=mapped_column(String(24),default='g')
    usage_unit:Mapped[str]=mapped_column(String(24),default='g')
class Unit(Base):
    __tablename__='units'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    code:Mapped[str]=mapped_column(String(24))
    dimension:Mapped[str]=mapped_column(String(16))
    factor:Mapped[str]=mapped_column(String(40))

class Recipe(Base):
    __tablename__='recipes'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    name:Mapped[str]=mapped_column(String(120))
    yield_qty:Mapped[str]=mapped_column(String(40))
    yield_unit:Mapped[str]=mapped_column(String(20),default='pcs')
    items_json:Mapped[str]=mapped_column(Text,default='[]')
    packaging_cost:Mapped[str]=mapped_column(String(40),default='0')
    labor_cost:Mapped[str]=mapped_column(String(40),default='0')
    overhead_cost:Mapped[str]=mapped_column(String(40),default='0')

class IngredientPriceHistory(Base):
    __tablename__='ingredient_price_history'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    ingredient_id:Mapped[int]=mapped_column(Integer,index=True)
    price_per_unit:Mapped[str]=mapped_column(String(40))
    created_at:Mapped[int]=mapped_column(Integer)
class ExportUsage(Base):
    __tablename__='export_usage'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    month:Mapped[str]=mapped_column(String(7),index=True)
    kind:Mapped[str]=mapped_column(String(10))
    created_at:Mapped[int]=mapped_column(Integer)

app=FastAPI(title='KalkuResep Cloud',version='2.4.0')
_attempts={}
def throttle(request:Request, action:str):
    # In-memory guard for single-instance MVP. Use Redis for multiple replicas.
    now=time.monotonic(); key=(request.client.host if request.client else 'unknown',action)
    values=[t for t in _attempts.get(key,[]) if now-t<300]
    if len(values)>= (15 if action=='login' else 8): raise HTTPException(429,'Terlalu banyak percobaan. Coba lagi nanti.')
    values.append(now);_attempts[key]=values

@app.on_event('startup')
def startup():
    Base.metadata.create_all(engine) # MVP schema; use Alembic for later migrations
    # One-time additive migration for users upgrading from Cloud v2.0.
    with engine.begin() as conn:
        if engine.dialect.name == 'sqlite':
            cols=lambda table: {row[1] for row in conn.execute(text(f'PRAGMA table_info({table})'))}
        else:
            cols=lambda table: {row[0] for row in conn.execute(text('SELECT column_name FROM information_schema.columns WHERE table_name=:table'), {'table':table})}
        if 'pro_expires_at' not in cols('users'):
            conn.execute(text('ALTER TABLE users ADD COLUMN pro_expires_at INTEGER'))
        for field,default in (('purchase_qty','1'),('purchase_unit',''),('usage_unit','')):
            if field not in cols('ingredients'):
                conn.execute(text(f"ALTER TABLE ingredients ADD COLUMN {field} VARCHAR(40) DEFAULT '{default}' NOT NULL"))
        # Preserve historical prices: old price_per_unit refers to the old ingredient.unit.
        conn.execute(text("UPDATE ingredients SET purchase_unit=unit WHERE purchase_unit=''"))
        conn.execute(text("UPDATE ingredients SET usage_unit=unit WHERE usage_unit=''"))
        for field in ('packaging_cost','labor_cost','overhead_cost'):
            if field not in cols('recipes'):
                conn.execute(text(f"ALTER TABLE recipes ADD COLUMN {field} VARCHAR(40) DEFAULT '0' NOT NULL"))
    email=os.getenv('ADMIN_EMAIL','').strip().lower(); password=os.getenv('ADMIN_PASSWORD','')
    if email and password and len(password)>=16:
        with SessionLocal.begin() as db:
            existing=db.scalar(select(User).where(User.email==email))
            if not existing:
                db.add(User(email=email,password_hash=hash_password(password),plan='pro',is_admin=True))
            elif existing.is_admin and os.getenv('ADMIN_SYNC_PASSWORD','false').lower()=='true':
                existing.password_hash=hash_password(password)
            elif not existing.is_admin:
                # Admin bootstrap must not silently promote an account registered by someone else.
                raise RuntimeError('ADMIN_EMAIL sudah terdaftar sebagai akun biasa. Gunakan email admin lain atau migrasi akun secara terverifikasi.')

def hash_password(password):
    salt=secrets.token_bytes(16)
    dk=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
    return 'scrypt$'+salt.hex()+'$'+dk.hex()
def verify_password(password,stored):
    try:
        _,salt,digest=stored.split('$'); actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1)
        return hmac.compare_digest(actual,bytes.fromhex(digest))
    except (ValueError,TypeError): return False

def db_session():
    with SessionLocal() as db: yield db

def current_user(request:Request,db:Session=Depends(db_session)):
    token=request.cookies.get('kr_session','')
    if not token: raise HTTPException(401,'Silakan login')
    s=db.scalar(select(LoginSession).where(LoginSession.token_hash==hashlib.sha256(token.encode()).hexdigest(),LoginSession.expires>int(time.time())))
    if not s: raise HTTPException(401,'Sesi berakhir')
    user=db.get(User,s.user_id)
    if not user or not user.active: raise HTTPException(403,'Akun nonaktif')
    return user

def require_admin(user:User=Depends(current_user)):
    if not user.is_admin: raise HTTPException(403,'Admin only')
    return user

@app.middleware('http')
async def security(request:Request,call_next):
    if request.method in ('POST','PUT','PATCH','DELETE'):
        origin=request.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            o=urlsplit(origin); host=request.headers.get('host','')
            if o.netloc!=host or o.scheme not in ('http','https'):
                return JSONResponse({'detail':'Invalid origin'},status_code=403)
        elif request.cookies.get('kr_session'):
            return JSONResponse({'detail':'Origin required'},status_code=403)
        if request.headers.get('content-type','').split(';')[0]!='application/json':
            return JSONResponse({'detail':'JSON required'},status_code=415)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store' if request.url.path in ('/','/static/app.js','/static/style.css') or request.url.path.startswith('/api/') else 'public, max-age=3600'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    return response

class Credentials(BaseModel):
    email:str=Field(min_length=5,max_length=254)
    password:str=Field(min_length=12,max_length=256)
class IngredientIn(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    purchase_qty:str='1'
    purchase_unit:str=Field(min_length=1,max_length=24)
    usage_unit:str=Field(min_length=1,max_length=24)
    price_per_unit:str
class UnitIn(BaseModel):
    code:str=Field(min_length=1,max_length=24,pattern=r'^[a-zA-Z0-9_-]+$')
    dimension:str=Field(pattern='^(mass|volume|count)$')
    factor:str

BUILTIN_UNITS={'g':('mass',Decimal('1')),'kg':('mass',Decimal('1000')),'mg':('mass',Decimal('0.001')),
               'ml':('volume',Decimal('1')),'l':('volume',Decimal('1000')),
               'pcs':('count',Decimal('1')),'lusin':('count',Decimal('12'))}
def unit_info(db,u,code):
    if code in BUILTIN_UNITS:return BUILTIN_UNITS[code]
    x=db.scalar(select(Unit).where(Unit.owner_id==u.id,Unit.code==code))
    if not x:raise HTTPException(422,f'Satuan {code} tidak ditemukan')
    return x.dimension,dec(x.factor,True)
def conversion(db,u,source,dest):
    sd,sf=unit_info(db,u,source);dd,df=unit_info(db,u,dest)
    if sd!=dd:raise HTTPException(422,f'Satuan {source} dan {dest} tidak kompatibel')
    return sf/df
@app.get('/api/units')
def list_units(u:User=Depends(current_user),db:Session=Depends(db_session)):
    return [{'code':k,'dimension':v[0],'factor':str(v[1]),'builtin':True} for k,v in BUILTIN_UNITS.items()]+[{'code':x.code,'dimension':x.dimension,'factor':x.factor,'builtin':False} for x in db.scalars(select(Unit).where(Unit.owner_id==u.id).order_by(Unit.code))]
@app.post('/api/units')
def create_unit(data:UnitIn,u:User=Depends(current_user),db:Session=Depends(db_session)):
    code=data.code.strip().lower()
    if code in BUILTIN_UNITS or db.scalar(select(Unit).where(Unit.owner_id==u.id,Unit.code==code)):
        raise HTTPException(409,'Kode satuan sudah digunakan')
    factor=dec(data.factor,True)
    db.add(Unit(owner_id=u.id,code=code,dimension=data.dimension,factor=str(factor)));db.commit()
    return {'code':code}
@app.delete('/api/units/{code}')
def remove_unit(code:str,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Unit).where(Unit.owner_id==u.id,Unit.code==code))
    if not x:raise HTTPException(404,'Satuan tidak ditemukan atau satuan bawaan')
    if db.scalar(select(Ingredient.id).where(Ingredient.owner_id==u.id,((Ingredient.purchase_unit==code)|(Ingredient.usage_unit==code))).limit(1)):
        raise HTTPException(409,'Satuan masih digunakan oleh bahan')
    for r in db.scalars(select(Recipe).where(Recipe.owner_id==u.id)):
        if any(item.get('unit')==code for item in json.loads(r.items_json)):
            raise HTTPException(409,'Satuan masih digunakan dalam resep')
    db.delete(x);db.commit();return {'ok':True}
class RecipeIn(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    yield_qty:str
    yield_unit:str=Field(default='pcs',max_length=24)
    items:list[dict]=Field(default_factory=list,max_length=100)
    packaging_cost:str='0'
    labor_cost:str='0'
    overhead_cost:str='0'

def dec(value,positive=False):
    try: d=Decimal(str(value))
    except (InvalidOperation,ValueError): raise HTTPException(422,'Angka tidak valid')
    if not d.is_finite() or d<0 or (positive and d==0) or d>Decimal('1000000000000'): raise HTTPException(422,'Angka di luar batas')
    return d

def effective_plan(u):
    return 'pro' if u.is_admin or (u.plan=='pro' and u.pro_expires_at is not None and u.pro_expires_at>int(time.time())) else 'free'
def require_pro(u):
    if effective_plan(u)!='pro': raise HTTPException(403,'Fitur khusus Pro. Silakan upgrade.')
def export_permission(u,db,kind):
    if effective_plan(u)=='pro': return
    if kind=='excel': raise HTTPException(403,'Ekspor Excel hanya untuk Pro')
    month=datetime.now(timezone.utc).strftime('%Y-%m')
    count=db.scalar(select(func.count()).select_from(ExportUsage).where(ExportUsage.owner_id==u.id,ExportUsage.month==month,ExportUsage.kind=='pdf'))
    if count>=3: raise HTTPException(403,'Batas 3 ekspor PDF per bulan pada paket Free tercapai')
def record_export(u,db,kind):
    db.add(ExportUsage(owner_id=u.id,month=datetime.now(timezone.utc).strftime('%Y-%m'),kind=kind,created_at=int(time.time())))
    db.commit()
def user_view(u): return {'id':u.id,'email':u.email,'plan':effective_plan(u),'is_admin':u.is_admin,'active':u.active,'pro_expires_at':u.pro_expires_at,'limits':{'recipes':None if effective_plan(u)=='pro' else 5,'ingredients':None if effective_plan(u)=='pro' else 30,'pdf_per_month':None if effective_plan(u)=='pro' else 3,'excel':effective_plan(u)=='pro'}}

def issue_cookie(resp,db,user):
    token=secrets.token_urlsafe(32)
    db.add(LoginSession(user_id=user.id,token_hash=hashlib.sha256(token.encode()).hexdigest(),expires=int(time.time())+604800));db.commit()
    resp.set_cookie('kr_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE','true').lower()=='true',samesite='strict',max_age=604800,path='/')

@app.post('/api/register')
def register(data:Credentials,request:Request,db:Session=Depends(db_session)):
    throttle(request,'register')
    email=data.email.strip().lower()
    if '@' not in email: raise HTTPException(422,'Email tidak valid')
    if db.scalar(select(User).where(User.email==email)): raise HTTPException(409,'Email sudah terdaftar')
    u=User(email=email,password_hash=hash_password(data.password),plan='free');db.add(u);db.commit()
    return {'message':'Akun dibuat, silakan login'}
@app.post('/api/login')
def login(data:Credentials,request:Request,db:Session=Depends(db_session)):
    throttle(request,'login')
    u=db.scalar(select(User).where(User.email==data.email.strip().lower()))
    if not u:
        verify_password(data.password,hash_password('dummy-password-to-balance-timing'))
        raise HTTPException(401,'Email atau password salah')
    if not verify_password(data.password,u.password_hash): raise HTTPException(401,'Email atau password salah')
    if not u.active: raise HTTPException(403,'Akun nonaktif')
    resp=JSONResponse({'user':user_view(u)});issue_cookie(resp,db,u);return resp
@app.post('/api/logout')
def logout(request:Request,db:Session=Depends(db_session)):
    token=request.cookies.get('kr_session','')
    db.query(LoginSession).filter(LoginSession.token_hash==hashlib.sha256(token.encode()).hexdigest()).delete();db.commit()
    resp=JSONResponse({'ok':True});resp.delete_cookie('kr_session',path='/');return resp
@app.get('/api/me')
def me(u:User=Depends(current_user)): return user_view(u)

@app.get('/api/ingredients')
def ingredients(u:User=Depends(current_user),db:Session=Depends(db_session)):
    return [{'id':x.id,'name':x.name,'unit':x.usage_unit,'purchase_qty':x.purchase_qty,'purchase_unit':x.purchase_unit,'usage_unit':x.usage_unit,'price_per_unit':x.price_per_unit} for x in db.scalars(select(Ingredient).where(Ingredient.owner_id==u.id).order_by(Ingredient.name))]
@app.post('/api/ingredients')
def add_ingredient(data:IngredientIn,u:User=Depends(current_user),db:Session=Depends(db_session)):
    count=db.scalar(select(func.count()).select_from(Ingredient).where(Ingredient.owner_id==u.id))
    if effective_plan(u)=='free' and count>=30:raise HTTPException(403,'Batas 30 bahan paket Free tercapai')
    conversion(db,u,data.purchase_unit,data.usage_unit)
    qty=dec(data.purchase_qty,True);price=dec(data.price_per_unit)
    x=Ingredient(owner_id=u.id,name=data.name.strip(),unit=data.usage_unit,price_per_unit=str(price),purchase_qty=str(qty),purchase_unit=data.purchase_unit,usage_unit=data.usage_unit)
    db.add(x);db.flush();db.add(IngredientPriceHistory(owner_id=u.id,ingredient_id=x.id,price_per_unit=str(price),created_at=int(time.time())));db.commit();return {'id':x.id}
@app.delete('/api/ingredients/{item_id}')
def delete_ingredient(item_id:int,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Ingredient).where(Ingredient.id==item_id,Ingredient.owner_id==u.id))
    if not x: raise HTTPException(404,'Bahan tidak ditemukan')
    db.delete(x);db.commit();return {'ok':True}

def recipe_view(x): return {'id':x.id,'name':x.name,'yield_qty':x.yield_qty,'yield_unit':x.yield_unit,'items':json.loads(x.items_json),'packaging_cost':x.packaging_cost,'labor_cost':x.labor_cost,'overhead_cost':x.overhead_cost}
def validate_recipe(data,db,u):
    dec(data.yield_qty,True)
    normalized=[]
    for row in data.items:
        try: ingredient_id=int(row['ingredient_id']);qty=dec(row['qty'])
        except (KeyError,ValueError,TypeError): raise HTTPException(422,'Item bahan tidak valid')
        ing=db.scalar(select(Ingredient).where(Ingredient.id==ingredient_id,Ingredient.owner_id==u.id))
        if not ing: raise HTTPException(422,'Bahan bukan milik akun ini')
        unit=str(row.get('unit') or ing.usage_unit)
        conversion(db,u,unit,ing.purchase_unit)
        normalized.append({'ingredient_id':ingredient_id,'qty':str(qty),'unit':unit})
    return normalized
@app.get('/api/recipes')
def recipes(u:User=Depends(current_user),db:Session=Depends(db_session)):
    return [recipe_view(x) for x in db.scalars(select(Recipe).where(Recipe.owner_id==u.id).order_by(Recipe.id.desc()))]
@app.post('/api/recipes')
def add_recipe(data:RecipeIn,u:User=Depends(current_user),db:Session=Depends(db_session)):
    items=validate_recipe(data,db,u)
    count=db.scalar(select(func.count()).select_from(Recipe).where(Recipe.owner_id==u.id))
    if effective_plan(u)=='free' and count>=5: raise HTTPException(403,'Batas paket Free tercapai')
    x=Recipe(owner_id=u.id,name=data.name.strip(),yield_qty=str(dec(data.yield_qty,True)),yield_unit=data.yield_unit,items_json=json.dumps(items),packaging_cost=str(dec(data.packaging_cost)),labor_cost=str(dec(data.labor_cost)),overhead_cost=str(dec(data.overhead_cost)));db.add(x);db.commit();return {'id':x.id}
@app.put('/api/recipes/{recipe_id}')
def update_recipe(recipe_id:int,data:RecipeIn,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    items=validate_recipe(data,db,u)
    x.name=data.name.strip();x.yield_qty=str(dec(data.yield_qty,True));x.yield_unit=data.yield_unit;x.items_json=json.dumps(items);x.packaging_cost=str(dec(data.packaging_cost));x.labor_cost=str(dec(data.labor_cost));x.overhead_cost=str(dec(data.overhead_cost));db.commit();return {'ok':True}
@app.delete('/api/recipes/{recipe_id}')
def delete_recipe(recipe_id:int,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    db.delete(x);db.commit();return {'ok':True}
@app.post('/api/recipes/{recipe_id}/duplicate')
def duplicate_recipe(recipe_id:int,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u)
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    y=Recipe(owner_id=u.id,name=(x.name[:110]+' (Salinan)'),yield_qty=x.yield_qty,yield_unit=x.yield_unit,items_json=x.items_json,packaging_cost=x.packaging_cost,labor_cost=x.labor_cost,overhead_cost=x.overhead_cost)
    db.add(y);db.commit();return {'id':y.id}
class IngredientPriceUpdate(BaseModel):
    price_per_unit:str
@app.put('/api/ingredients/{item_id}/price')
def update_ingredient_price(item_id:int,data:IngredientPriceUpdate,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Ingredient).where(Ingredient.id==item_id,Ingredient.owner_id==u.id))
    if not x: raise HTTPException(404,'Bahan tidak ditemukan')
    price=dec(data.price_per_unit)
    x.price_per_unit=str(price)
    db.add(IngredientPriceHistory(owner_id=u.id,ingredient_id=x.id,price_per_unit=str(price),created_at=int(time.time())))
    db.commit();return {'ok':True}
@app.get('/api/ingredients/{item_id}/price-history')
def ingredient_price_history(item_id:int,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u)
    x=db.scalar(select(Ingredient).where(Ingredient.id==item_id,Ingredient.owner_id==u.id))
    if not x: raise HTTPException(404,'Bahan tidak ditemukan')
    return [{'price_per_unit':r.price_per_unit,'created_at':r.created_at} for r in db.scalars(select(IngredientPriceHistory).where(IngredientPriceHistory.owner_id==u.id,IngredientPriceHistory.ingredient_id==item_id).order_by(IngredientPriceHistory.id.desc()).limit(100))]
@app.get('/api/recipes/compare-hpp')
def compare_hpp(u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u)
    output=[]
    for r in db.scalars(select(Recipe).where(Recipe.owner_id==u.id).order_by(Recipe.id.desc()).limit(100)):
        result=calculate(r.id,r.yield_qty,'0',u,db)
        output.append({'id':r.id,'name':r.name,'yield_qty':r.yield_qty,'yield_unit':r.yield_unit,'hpp_per_unit':result['hpp_per_unit'],'total_cost':result['total_cost']})
    return output

@app.get('/api/recipes/{recipe_id}/calculate')
def calculate(recipe_id:int,target:str='1',sell_price:str='0',u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    factor=dec(target,True)/dec(x.yield_qty,True);selling=dec(sell_price)
    items=[];total=Decimal(0);flour=Decimal(0)
    for row in json.loads(x.items_json):
        ing=db.scalar(select(Ingredient).where(Ingredient.id==row['ingredient_id'],Ingredient.owner_id==u.id))
        if not ing:raise HTTPException(409,'Resep memiliki bahan yang sudah dihapus; perbaiki resep dahulu')
        unit=row.get('unit') or ing.usage_unit
        qty=dec(row['qty'])*factor
        # price_per_unit is the price of purchase_qty purchase_unit (e.g. Rp 14.000 / 1 kg).
        cost=qty*conversion(db,u,unit,ing.purchase_unit)/dec(ing.purchase_qty,True)*dec(ing.price_per_unit)
        total+=cost
        dim,_=unit_info(db,u,unit)
        grams=dec(row['qty'])*conversion(db,u,unit,'g') if dim=='mass' else None
        if grams is not None and 'tepung' in ing.name.lower():flour+=grams
        items.append({'name':ing.name,'unit':unit,'qty':str(qty),'cost':str(cost),'base_grams':grams})
    for item in items:
        grams=item.pop('base_grams')
        item['bakers_pct']=str(grams/flour*100) if flour and grams is not None else None
    extra={k:dec(getattr(x,k))*factor for k in ('packaging_cost','labor_cost','overhead_cost')}
    full=total+sum(extra.values(),Decimal(0))
    hpp=full/dec(target,True)
    return {'items':items,'ingredient_cost':str(total),'extra_costs':{k:str(v) for k,v in extra.items()},'total_cost':str(full),'hpp_per_unit':str(hpp),'selling_price':str(selling),'profit_per_unit':str(selling-hpp),'margin_pct':str((selling-hpp)/selling*100) if selling else None,'factor':str(factor),'note':'HPP lengkap mencakup biaya bahan, kemasan, tenaga kerja dan overhead berdasarkan input resep dasar'}

@app.get('/api/admin/users')
def admin_users(admin:User=Depends(require_admin),db:Session=Depends(db_session)):
    return [user_view(u) for u in db.scalars(select(User).order_by(User.id.desc()).limit(500))]
class PlanChange(BaseModel):
    plan:str=Field(pattern='^(free|pro)$')
    duration_days:int=Field(default=365,ge=1,le=3650)
@app.put('/api/admin/users/{user_id}/plan')
def change_plan(user_id:int,data:PlanChange,admin:User=Depends(require_admin),db:Session=Depends(db_session)):
    u=db.get(User,user_id)
    if not u: raise HTTPException(404,'Pengguna tidak ditemukan')
    if u.is_admin and data.plan!='pro': raise HTTPException(400,'Paket admin tidak boleh diturunkan')
    u.plan=data.plan
    u.pro_expires_at=(int(time.time())+data.duration_days*86400) if data.plan=='pro' and not u.is_admin else None
    db.commit();return user_view(u)
@app.get('/health')
def health():
    from sqlalchemy import text
    with engine.connect() as conn: conn.execute(text('SELECT 1'))
    return {'status':'ok'}

@app.get('/',response_class=HTMLResponse)
def index(): return (Path(__file__).resolve().parent.parent/'static/index.html').read_text(encoding='utf-8')
from fastapi.staticfiles import StaticFiles
app.mount('/static',StaticFiles(directory=Path(__file__).resolve().parent.parent/'static'),name='static')


@app.get('/api/recipes/{recipe_id}/export.xlsx')
def export_xlsx(recipe_id:int,target:str='1',sell_price:str='0',u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u)
    from io import BytesIO
    from openpyxl import Workbook
    from fastapi.responses import StreamingResponse
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    result=calculate(recipe_id,target,sell_price,u,db)
    wb=Workbook();ws=wb.active;ws.title='Kalkulasi'
    ws.append(['KalkuResep - '+x.name]);ws.append(['Target',target,x.yield_unit]);ws.append(['Bahan','Jumlah','Satuan','Biaya (Rp)',"Baker's %"])
    for row in result['items']:
        ws.append([row['name'],float(row['qty']),row['unit'],float(row['cost']),float(row['bakers_pct']) if row['bakers_pct'] is not None else None])
    for key,label in [('ingredient_cost','Biaya bahan'),('total_cost','Total HPP'),('hpp_per_unit','HPP per unit'),('selling_price','Harga jual'),('profit_per_unit','Laba per unit')]:
        ws.append([label,float(result[key])])
    for col,width in {'A':32,'B':19,'C':15,'D':22,'E':17}.items():ws.column_dimensions[col].width=width
    stream=BytesIO();wb.save(stream);stream.seek(0);record_export(u,db,'excel')
    return StreamingResponse(stream,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="kalkuresep-{recipe_id}.xlsx"'})

@app.get('/api/recipes/{recipe_id}/export.pdf')
def export_pdf(recipe_id:int,target:str='1',sell_price:str='0',u:User=Depends(current_user),db:Session=Depends(db_session)):
    export_permission(u,db,'pdf')
    from io import BytesIO
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from fastapi.responses import StreamingResponse
    from xml.sax.saxutils import escape
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    r=calculate(recipe_id,target,sell_price,u,db)
    out=BytesIO();doc=SimpleDocTemplate(out);styles=getSampleStyleSheet()
    story=[Paragraph('KalkuResep - '+escape(x.name),styles['Title']),Paragraph('Target: '+escape(target)+' '+escape(x.yield_unit),styles['Normal']),Spacer(1,14)]
    data=[['Bahan','Jumlah','Satuan','Biaya Rp',"Baker's %"]]
    for item in r['items']:data.append([item['name'],item['qty'],item['unit'],item['cost'],item['bakers_pct'] or '-'])
    t=Table(data,repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#087D68')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),0.4,colors.lightgrey),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story.append(t)
    for key,label in [('ingredient_cost','Biaya bahan'),('total_cost','Total HPP'),('hpp_per_unit','HPP per unit'),('selling_price','Harga jual'),('profit_per_unit','Laba per unit')]:story.append(Paragraph(label+': Rp '+escape(r[key]),styles['Normal']))
    doc.build(story);out.seek(0);record_export(u,db,'pdf')
    return StreamingResponse(out,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="kalkuresep-{recipe_id}.pdf"'})
