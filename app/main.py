import os, json, secrets, hashlib, hmac, time
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File
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

class RecipeComponent(Base):
    __tablename__='recipe_components'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    parent_id:Mapped[int]=mapped_column(ForeignKey('recipes.id'),index=True)
    child_id:Mapped[int]=mapped_column(ForeignKey('recipes.id'),index=True)
    qty:Mapped[str]=mapped_column(String(40))

class RecipeSellingPrice(Base):
    __tablename__='recipe_selling_prices'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    recipe_id:Mapped[int]=mapped_column(ForeignKey('recipes.id'),index=True)
    price:Mapped[str]=mapped_column(String(40))

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

app=FastAPI(title='KalkuResep Cloud',version='2.6.0')
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
        if request.headers.get('content-type','').split(';')[0] != ('multipart/form-data' if request.url.path=='/api/pro/import/preview' else 'application/json'):
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
    components:list[dict]=Field(default_factory=list,max_length=30)
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
@app.put('/api/ingredients/{item_id}')
def edit_ingredient(item_id:int,data:IngredientIn,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Ingredient).where(Ingredient.id==item_id,Ingredient.owner_id==u.id))
    if not x: raise HTTPException(404,'Bahan tidak ditemukan')
    conversion(db,u,data.purchase_unit,data.usage_unit)
    qty=dec(data.purchase_qty,True);price=dec(data.price_per_unit)
    changed_price=dec(x.price_per_unit)!=price
    x.name=data.name.strip();x.purchase_qty=str(qty);x.purchase_unit=data.purchase_unit
    x.usage_unit=data.usage_unit;x.unit=data.usage_unit;x.price_per_unit=str(price)
    if changed_price: db.add(IngredientPriceHistory(owner_id=u.id,ingredient_id=x.id,price_per_unit=str(price),created_at=int(time.time())))
    db.commit();return {'id':x.id,'ok':True}

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
def validate_components(data,db,u,parent_id=None):
    if data.components: require_pro(u)
    normalized=[]
    for row in data.components:
        try: child_id=int(row['recipe_id']); qty=dec(row['qty'],True)
        except (ValueError,TypeError,KeyError): raise HTTPException(422,'Sub-resep tidak valid')
        child=db.scalar(select(Recipe).where(Recipe.id==child_id,Recipe.owner_id==u.id))
        if not child or child_id==parent_id: raise HTTPException(422,'Sub-resep tidak ditemukan atau mengacu ke diri sendiri')
        normalized.append((child_id,str(qty)))
    return normalized
def set_components(db,u,recipe_id,components):
    db.query(RecipeComponent).filter(RecipeComponent.owner_id==u.id,RecipeComponent.parent_id==recipe_id).delete()
    for child_id,qty in components:
        db.add(RecipeComponent(owner_id=u.id,parent_id=recipe_id,child_id=child_id,qty=qty))
@app.get('/api/recipes')
def recipes(u:User=Depends(current_user),db:Session=Depends(db_session)):
    return [recipe_view(x) for x in db.scalars(select(Recipe).where(Recipe.owner_id==u.id).order_by(Recipe.id.desc()))]
@app.post('/api/recipes')
def add_recipe(data:RecipeIn,u:User=Depends(current_user),db:Session=Depends(db_session)):
    items=validate_recipe(data,db,u)
    components=validate_components(data,db,u)
    count=db.scalar(select(func.count()).select_from(Recipe).where(Recipe.owner_id==u.id))
    if effective_plan(u)=='free' and count>=5: raise HTTPException(403,'Batas paket Free tercapai')
    x=Recipe(owner_id=u.id,name=data.name.strip(),yield_qty=str(dec(data.yield_qty,True)),yield_unit=data.yield_unit,items_json=json.dumps(items),packaging_cost=str(dec(data.packaging_cost)),labor_cost=str(dec(data.labor_cost)),overhead_cost=str(dec(data.overhead_cost)));db.add(x);db.flush();set_components(db,u,x.id,components);db.commit();return {'id':x.id}
@app.put('/api/recipes/{recipe_id}')
def update_recipe(recipe_id:int,data:RecipeIn,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    items=validate_recipe(data,db,u)
    components=validate_components(data,db,u,recipe_id)
    x.name=data.name.strip();x.yield_qty=str(dec(data.yield_qty,True));x.yield_unit=data.yield_unit;x.items_json=json.dumps(items);x.packaging_cost=str(dec(data.packaging_cost));x.labor_cost=str(dec(data.labor_cost));x.overhead_cost=str(dec(data.overhead_cost));set_components(db,u,x.id,components);db.commit();return {'ok':True}
@app.delete('/api/recipes/{recipe_id}')
def delete_recipe(recipe_id:int,u:User=Depends(current_user),db:Session=Depends(db_session)):
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    if db.scalar(select(RecipeComponent.id).where(RecipeComponent.owner_id==u.id,RecipeComponent.child_id==recipe_id).limit(1)): raise HTTPException(409,'Resep digunakan sebagai sub-resep. Lepaskan dahulu.')
    db.query(RecipeComponent).filter(RecipeComponent.owner_id==u.id,RecipeComponent.parent_id==recipe_id).delete()
    db.query(RecipeSellingPrice).filter(RecipeSellingPrice.owner_id==u.id,RecipeSellingPrice.recipe_id==recipe_id).delete()
    db.delete(x);db.commit();return {'ok':True}
@app.post('/api/recipes/{recipe_id}/duplicate')
def duplicate_recipe(recipe_id:int,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u)
    x=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not x: raise HTTPException(404,'Resep tidak ditemukan')
    y=Recipe(owner_id=u.id,name=(x.name[:110]+' (Salinan)'),yield_qty=x.yield_qty,yield_unit=x.yield_unit,items_json=x.items_json,packaging_cost=x.packaging_cost,labor_cost=x.labor_cost,overhead_cost=x.overhead_cost)
    db.add(y);db.flush();set_components(db,u,y.id,[(c.child_id,c.qty) for c in db.scalars(select(RecipeComponent).where(RecipeComponent.owner_id==u.id,RecipeComponent.parent_id==recipe_id))]);db.commit();return {'id':y.id}
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
    if recipe_id in getattr(db,'_kr_stack',[]) or len(getattr(db,'_kr_stack',[]))>=12: raise HTTPException(422,'Siklus sub-resep terdeteksi')
    factor=dec(target,True)/dec(x.yield_qty,True);selling=dec(sell_price)
    items=[];total=Decimal(0);flour=Decimal(0);missing_ingredients=[]
    for row in json.loads(x.items_json):
        ing=db.scalar(select(Ingredient).where(Ingredient.id==row['ingredient_id'],Ingredient.owner_id==u.id))
        if not ing:
            missing_ingredients.append(row['ingredient_id'])
            continue
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
    # Sub-recipes are costed per child yield unit; recursion rejects cycles and excessive depth.
    stack=getattr(db,'_kr_stack',[])
    if recipe_id in stack or len(stack)>=12: raise HTTPException(422,'Siklus sub-resep terdeteksi atau kedalaman terlalu besar')
    db._kr_stack=stack+[recipe_id]
    try:
        for component in db.scalars(select(RecipeComponent).where(RecipeComponent.owner_id==u.id,RecipeComponent.parent_id==recipe_id)):
            child=db.scalar(select(Recipe).where(Recipe.id==component.child_id,Recipe.owner_id==u.id))
            if not child: raise HTTPException(409,'Sub-resep tidak ditemukan')
            quantity=dec(component.qty,True)*factor
            sub=calculate(child.id,str(quantity),'0',u,db)
            cost=dec(sub['total_cost']);total+=cost
            items.append({'name':'↳ '+child.name,'unit':child.yield_unit,'qty':str(quantity),'cost':str(cost),'bakers_pct':None})
    finally:
        db._kr_stack=stack
    extra={k:dec(getattr(x,k))*factor for k in ('packaging_cost','labor_cost','overhead_cost')}
    full=total+sum(extra.values(),Decimal(0))
    hpp=full/dec(target,True)
    return {'items':items,'ingredient_cost':str(total),'extra_costs':{k:str(v) for k,v in extra.items()},'total_cost':str(full),'hpp_per_unit':str(hpp),'selling_price':str(selling),'profit_per_unit':str(selling-hpp),'margin_pct':str((selling-hpp)/selling*100) if selling else None,'factor':str(factor),'missing_ingredients':missing_ingredients,'note':'HPP lengkap mencakup biaya bahan, kemasan, tenaga kerja dan overhead berdasarkan input resep dasar'}

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

class PriceScenario(BaseModel):
    recipe_id:int
    changes:dict[str,str]=Field(default_factory=dict,max_length=100)
class SellingPriceInput(BaseModel):
    recipe_id:int
    mode:str=Field(pattern='^(margin|markup|food_cost)$')
    target_pct:str
    rounding:int=Field(default=500,ge=1,le=100000)
    save:bool=False
class PriceSet(BaseModel):
    price:str

def recipe_owned(db,u,recipe_id):
    r=db.scalar(select(Recipe).where(Recipe.id==recipe_id,Recipe.owner_id==u.id))
    if not r: raise HTTPException(404,'Resep tidak ditemukan')
    return r

@app.get('/api/pro/recipes/{recipe_id}/components')
def get_components(recipe_id:int,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u);recipe_owned(db,u,recipe_id)
    return [{'recipe_id':c.child_id,'qty':c.qty} for c in db.scalars(select(RecipeComponent).where(RecipeComponent.owner_id==u.id,RecipeComponent.parent_id==recipe_id))]

@app.post('/api/pro/scenario')
def price_scenario(data:PriceScenario,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u);recipe_owned(db,u,data.recipe_id)
    baseline=calculate(data.recipe_id,recipe_owned(db,u,data.recipe_id).yield_qty,'0',u,db)
    # No database price updates; transient override is restored even on exception.
    originals=[]
    try:
        for key,value in data.changes.items():
            try: ingredient_id=int(key)
            except ValueError: raise HTTPException(422,'ID bahan tidak valid')
            ing=db.scalar(select(Ingredient).where(Ingredient.id==ingredient_id,Ingredient.owner_id==u.id))
            if not ing: raise HTTPException(422,'Bahan tidak ditemukan')
            try: pct=Decimal(str(value))
            except (InvalidOperation,ValueError): raise HTTPException(422,'Persentase tidak valid')
            if not pct.is_finite() or pct < -100 or pct > 1000: raise HTTPException(422,'Perubahan harus antara -100% dan 1000%')
            originals.append((ing,ing.price_per_unit))
            ing.price_per_unit=str(dec(ing.price_per_unit)*(1+pct/100))
        updated=calculate(data.recipe_id,recipe_owned(db,u,data.recipe_id).yield_qty,'0',u,db)
    finally:
        for ing,price in originals: ing.price_per_unit=price
        db.rollback()
    old=dec(baseline['total_cost']);new=dec(updated['total_cost'])
    return {'before':baseline,'after':updated,'difference':str(new-old),'difference_pct':str((new-old)/old*100) if old else None}

@app.post('/api/pro/selling-price')
def smart_selling(data:SellingPriceInput,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u);r=recipe_owned(db,u,data.recipe_id)
    hpp=dec(calculate(r.id,r.yield_qty,'0',u,db)['hpp_per_unit']);pct=dec(data.target_pct)
    if data.mode=='margin' and pct>=100: raise HTTPException(422,'Margin harus di bawah 100%')
    if data.mode=='food_cost' and (pct<=0 or pct>100): raise HTTPException(422,'Food cost harus 0-100%')
    raw=hpp/(1-pct/100) if data.mode=='margin' else hpp*(1+pct/100) if data.mode=='markup' else hpp/(pct/100)
    from decimal import ROUND_CEILING
    price=(raw/Decimal(data.rounding)).to_integral_value(rounding=ROUND_CEILING)*data.rounding
    if data.save:
        existing=db.scalar(select(RecipeSellingPrice).where(RecipeSellingPrice.owner_id==u.id,RecipeSellingPrice.recipe_id==r.id))
        if existing: existing.price=str(price)
        else: db.add(RecipeSellingPrice(owner_id=u.id,recipe_id=r.id,price=str(price)))
        db.commit()
    return {'hpp':str(hpp),'suggested_price':str(price),'gross_profit':str(price-hpp),'margin_pct':str((price-hpp)/price*100) if price else None,'food_cost_pct':str(hpp/price*100) if price else None}

@app.put('/api/pro/recipes/{recipe_id}/selling-price')
def set_selling_price(recipe_id:int,data:PriceSet,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u);recipe_owned(db,u,recipe_id);price=dec(data.price,True)
    existing=db.scalar(select(RecipeSellingPrice).where(RecipeSellingPrice.owner_id==u.id,RecipeSellingPrice.recipe_id==recipe_id))
    if existing: existing.price=str(price)
    else: db.add(RecipeSellingPrice(owner_id=u.id,recipe_id=recipe_id,price=str(price)))
    db.commit();return {'ok':True}

@app.get('/api/pro/profitability')
def profitability(u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u);rows=[]
    prices={p.recipe_id:dec(p.price) for p in db.scalars(select(RecipeSellingPrice).where(RecipeSellingPrice.owner_id==u.id))}
    for r in db.scalars(select(Recipe).where(Recipe.owner_id==u.id).order_by(Recipe.name)):
        result=calculate(r.id,r.yield_qty,'0',u,db);hpp=dec(result['hpp_per_unit']);price=prices.get(r.id)
        rows.append({'recipe_id':r.id,'name':r.name,'yield_qty':r.yield_qty,'yield_unit':r.yield_unit,'hpp':str(hpp),'price':str(price) if price is not None else None,'gross_profit':str(price-hpp) if price is not None else None,'margin_pct':str((price-hpp)/price*100) if price else None,'food_cost_pct':str(hpp/price*100) if price else None})
    return sorted(rows,key=lambda x:Decimal(x['gross_profit']) if x['gross_profit'] is not None else Decimal('-999999999999'),reverse=True)

@app.get('/api/pro/import/template')
def import_template(u:User=Depends(current_user)):
    require_pro(u)
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    wb=Workbook();ws=wb.active;ws.title='Bahan';ws.append(['nama','jumlah_beli','satuan_beli','satuan_pakai','harga_beli']);ws.append(['Tepung terigu','1','kg','g',14000])
    rs=wb.create_sheet('Resep');rs.append(['kode_resep','nama_resep','hasil','satuan_hasil','nama_bahan','jumlah_pakai','satuan_pakai','kemasan','tenaga_kerja','overhead']);rs.append(['R001','Roti Manis',20,'pcs','Tepung terigu',500,'g',2000,3000,1000])
    info=wb.create_sheet('Petunjuk');info.append(['KalkuResep v2.6 - Template Import']);info.append(['Hapus baris contoh sebelum impor data asli.']);info.append(['Bahan harus sudah ada atau diimpor pada file yang sama.']);info.append(['Resep dengan kode_resep sama digabungkan menjadi satu resep.']);info.append(['Gunakan satuan yang tersedia di Master Satuan.']);info.append(['Import hanya menambahkan data, tidak mengubah data lama.']);info.append(['Jangan unggah file dari sumber tidak tepercaya.'])
    for sheet in (ws,rs):
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='087D68')
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width=max(17,min(28,max(len(str(c.value or '')) for c in col)+3))
    out=BytesIO();wb.save(out);out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="KalkuResep_Template_Import_v2.6.xlsx"'})

# Preview tokens are server-side and tied to owner; data is revalidated on commit.
_import_previews={}
def parse_import(content,db,u):
    from openpyxl import load_workbook
    from io import BytesIO
    try: wb=load_workbook(BytesIO(content),read_only=True,data_only=True)
    except Exception: raise HTTPException(422,'File Excel tidak valid')
    if not {'Bahan','Resep'}.issubset(set(wb.sheetnames)): raise HTTPException(422,'Sheet Bahan dan Resep wajib tersedia')
    def rows(sheet,headers):
        it=wb[sheet].iter_rows(values_only=True);head=next(it,None)
        if head is None or tuple(str(x or '').strip() for x in head[:len(headers)])!=tuple(headers): raise HTTPException(422,'Kolom sheet '+sheet+' tidak sesuai template')
        result=[]
        for n,row in enumerate(it,2):
            if n>1001: raise HTTPException(422,'Maksimal 1000 baris per sheet')
            if not any(x is not None and str(x).strip() for x in row):continue
            result.append({k:str(row[i]).strip() if i<len(row) and row[i] is not None else '' for i,k in enumerate(headers)})
        return result
    ingredients=rows('Bahan',['nama','jumlah_beli','satuan_beli','satuan_pakai','harga_beli'])
    recipe_rows=rows('Resep',['kode_resep','nama_resep','hasil','satuan_hasil','nama_bahan','jumlah_pakai','satuan_pakai','kemasan','tenaga_kerja','overhead'])
    if not ingredients and not recipe_rows:raise HTTPException(422,'Template tidak berisi data')
    known={i.name.casefold():i for i in db.scalars(select(Ingredient).where(Ingredient.owner_id==u.id))}
    staged={}
    for row in ingredients:
        name=row['nama'];key=name.casefold()
        if not name or key in known or key in staged:raise HTTPException(422,'Nama bahan kosong atau duplikat: '+name)
        conversion(db,u,row['satuan_beli'],row['satuan_pakai']);dec(row['jumlah_beli'],True);dec(row['harga_beli'])
        staged[key]=row
    groups={}
    for row in recipe_rows:
        code=row['kode_resep'];name=row['nama_resep'];key=row['nama_bahan'].casefold()
        if not code or not name or key not in known and key not in staged:raise HTTPException(422,'Kode, resep, atau nama bahan tidak valid: '+str(row))
        dec(row['hasil'],True);dec(row['jumlah_pakai']);[dec(row[k]) for k in ('kemasan','tenaga_kerja','overhead')]
        source=known[key].purchase_unit if key in known else staged[key]['satuan_beli']
        conversion(db,u,row['satuan_pakai'],source)
        if code in groups and any(groups[code][k]!=row[k] for k in ('nama_resep','hasil','satuan_hasil','kemasan','tenaga_kerja','overhead')):raise HTTPException(422,'Data header resep tidak konsisten: '+code)
        groups.setdefault(code,{**row,'items':[]})['items'].append(row)
    if len(groups)>200:raise HTTPException(422,'Maksimal 200 resep per impor')
    return {'ingredients':ingredients,'recipes':list(groups.values())}

@app.post('/api/pro/import/preview')
async def preview_import(file:UploadFile=File(...),u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u)
    if not file.filename.lower().endswith('.xlsx'):raise HTTPException(422,'Hanya file .xlsx')
    content=await file.read(2_000_001)
    if len(content)>2_000_000:raise HTTPException(413,'Ukuran maksimal 2 MB')
    data=parse_import(content,db,u)
    token=secrets.token_urlsafe(24);now=time.time()
    for key,val in list(_import_previews.items()):
        if val[0]<now:del _import_previews[key]
    if len(_import_previews)>=30: _import_previews.pop(next(iter(_import_previews)))
    _import_previews[token]=(now+900,u.id,content)
    return {'token':token,'ingredients':len(data['ingredients']),'recipes':len(data['recipes']),'ingredient_names':[x['nama'] for x in data['ingredients'][:15]],'recipe_names':[x['nama_resep'] for x in data['recipes'][:15]]}

class ImportCommit(BaseModel):
    token:str
@app.post('/api/pro/import/commit')
def commit_import(data:ImportCommit,u:User=Depends(current_user),db:Session=Depends(db_session)):
    require_pro(u);saved=_import_previews.get(data.token)
    if not saved or saved[0]<time.time() or saved[1]!=u.id:raise HTTPException(410,'Pratinjau kedaluwarsa. Unggah ulang.')
    payload=parse_import(saved[2],db,u)
    try:
        known={i.name.casefold():i for i in db.scalars(select(Ingredient).where(Ingredient.owner_id==u.id))}
        for row in payload['ingredients']:
            x=Ingredient(owner_id=u.id,name=row['nama'],unit=row['satuan_pakai'],price_per_unit=str(dec(row['harga_beli'])),purchase_qty=str(dec(row['jumlah_beli'],True)),purchase_unit=row['satuan_beli'],usage_unit=row['satuan_pakai'])
            db.add(x);db.flush();known[x.name.casefold()]=x
            db.add(IngredientPriceHistory(owner_id=u.id,ingredient_id=x.id,price_per_unit=x.price_per_unit,created_at=int(time.time())))
        for row in payload['recipes']:
            items=[{'ingredient_id':known[i['nama_bahan'].casefold()].id,'qty':str(dec(i['jumlah_pakai'])),'unit':i['satuan_pakai']} for i in row['items']]
            db.add(Recipe(owner_id=u.id,name=row['nama_resep'],yield_qty=str(dec(row['hasil'],True)),yield_unit=row['satuan_hasil'],items_json=json.dumps(items),packaging_cost=str(dec(row['kemasan'])),labor_cost=str(dec(row['tenaga_kerja'])),overhead_cost=str(dec(row['overhead']))))
        db.commit()
    except Exception:
        db.rollback();raise
    del _import_previews[data.token]
    return {'ok':True,'ingredients':len(payload['ingredients']),'recipes':len(payload['recipes'])}

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
