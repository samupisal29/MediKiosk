import os, uuid, re, io
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError
from dotenv import load_dotenv
from pwdlib import PasswordHash
from PIL import Image, UnidentifiedImageError
import pytesseract
from triage import calculate_triage
from priority_queue import sort_patients
from demo_data import seed_demo_data

load_dotenv()
if os.getenv('TESSERACT_CMD'): pytesseract.pytesseract.tesseract_cmd = os.environ['TESSERACT_CMD']
from ocr import extract_text_from_image, regex_extract_information
FRONTEND = os.getenv('FRONTEND_ORIGIN', 'http://localhost:5173')
DATABASE_MODE = 'mongodb'
DEMO_MODE = os.getenv('DEMO_MODE', 'true').lower() == 'true'
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=2000)
try:
    client.admin.command('ping')
except PyMongoError:
    # Match EDIL's local development behavior while keeping the same collection API.
    # The in-memory database is intentionally ephemeral; MongoDB remains the persistent store.
    import mongomock
    client = mongomock.MongoClient()
    DATABASE_MODE = 'memory'
db = client[os.getenv('MONGODB_DATABASE', 'medikiosk')]
pwd = PasswordHash.recommended()
app = FastAPI(title='MediKiosk API', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
UPLOAD_DIR = Path(__file__).parent / 'uploads'; UPLOAD_DIR.mkdir(exist_ok=True)

def now(): return datetime.now(timezone.utc)
if DATABASE_MODE == 'memory' and DEMO_MODE:
    seed_demo_data(db, pwd, now)

def public(u): return {k:v for k,v in u.items() if k not in ('password_hash','_id')}
def audit(actor, action, subject=None): db.audit_events.insert_one({'actor_id': actor.get('id') if actor else None, 'hospital_id':actor.get('hospital_id') if actor else None, 'action':action, 'subject_id':subject, 'at':now()})
def current_user(request: Request):
    sid=request.cookies.get('mk_session')
    s=db.sessions.find_one({'token':sid, 'expires': {'$gt':now()}}) if sid else None
    u=db.users.find_one({'id':s['user_id']}) if s else None
    if not u or u.get('active') is False:
        if sid: db.sessions.delete_one({'token':sid})
        raise HTTPException(401,'Please sign in again.')
    result=public(u)
    if u.get('hospital_id'):
        hospital=db.hospitals.find_one({'id':u['hospital_id']})
        if hospital: result['hospital_name']=hospital.get('name')
    return result
def require(*roles):
    def dep(u=Depends(current_user)):
        if roles and u['role'] not in roles: raise HTTPException(403,'You do not have access to this action.')
        return u
    return dep
def patient_record(pid, user):
    if user['role']=='patient' and user['id'] != pid: raise HTTPException(404,'Record not found.')
    p=db.patients.find_one({'id':pid})
    if not p: raise HTTPException(404,'Patient not found.')
    return p

def hospital_public(row):
    fields=('id','name','location','phone','description','services')
    return {k:row[k] for k in fields if k in row}

def doctor_access(user, encounter):
    return user.get('role')=='doctor' and encounter.get('hospital_id')==user.get('hospital_id') and encounter.get('doctor_id')==user.get('id')

class Credentials(BaseModel): email:str; password:str
class LoginCredentials(Credentials): role:Optional[str]=None
class Register(Credentials): name:str=Field(min_length=2,max_length=100)
class DoctorSignup(BaseModel): name:str=Field(min_length=2,max_length=100); email:str; password:str=Field(min_length=12); hospital_id:str
class AdminSignup(BaseModel): name:str=Field(min_length=2,max_length=100); email:str; password:str=Field(min_length=12); hospital_name:str=Field(min_length=2,max_length=140); location:str=''; phone:str=''; description:str=''
class AppointmentCreate(BaseModel): hospital_id:str; doctor_id:str; scheduled_at:datetime; reason:str=Field(min_length=3,max_length=2000)
class AppointmentStatus(BaseModel): status:str
class MessageCreate(BaseModel): recipient_id:str; content:str=Field(min_length=1,max_length=2000)
class Intake(BaseModel): symptoms:str=Field(min_length=2,max_length=3000); age:int=Field(ge=0,le=120); allergies:str=''; history:str=''; urgency:str='routine'
class SOAP(BaseModel): subjective:str=''; objective:str=''; assessment:str=''; plan:str=''; summary:str=''; transcript:str=''
class ReleaseConfirmation(BaseModel): confirm_signature:bool=False
class Prescription(BaseModel): name:str; instructions:str=''
class Measurement(BaseModel): label:str; value:str
class Chat(BaseModel): message:str=Field(min_length=1,max_length=1000)
class DoctorCreate(BaseModel): name:str; email:str; password:str=Field(min_length=12)
class Contact(BaseModel): department:str; phone:str; hours:str; note:str=''
class DocumentReview(BaseModel): text:Optional[str]=None; verified:bool=True

@app.get('/api/health')
def health():
    if DATABASE_MODE == 'memory': return {'ok':True,'database':'memory'}
    try: client.admin.command('ping'); return {'ok':True,'database':'connected'}
    except PyMongoError: return {'ok':False,'database':'unavailable'}

@app.post('/api/register')
def register(data:Register, response:Response):
    email=data.email.lower().strip()
    if '@' not in email or len(data.password)<10: raise HTTPException(400,'Use a valid email and password of at least 10 characters.')
    if db.users.find_one({'email':email}): raise HTTPException(409,'An account with this email already exists.')
    uid=uuid.uuid4().hex; u={'id':uid,'name':data.name.strip(),'email':email,'role':'patient','created_at':now()}
    db.users.insert_one({**u,'password_hash':pwd.hash(data.password)}); db.patients.insert_one({'id':uid,'name':u['name'],'user_id':uid,'created_at':now()})
    login_user(u,response); audit(u,'patient_registered'); return public(u)

@app.post('/api/doctor-register')
def doctor_register(data:DoctorSignup):
    email=data.email.lower().strip()
    if '@' not in email: raise HTTPException(400,'Enter a valid email address.')
    if db.users.find_one({'email':email}): raise HTTPException(409,'An account with this email already exists.')
    hospital=db.hospitals.find_one({'id':data.hospital_id,'active':True})
    if not hospital: raise HTTPException(404,'Select an active hospital.')
    uid=uuid.uuid4().hex
    row={'id':uid,'name':data.name.strip(),'email':email,'role':'doctor','hospital_id':data.hospital_id,'active':False,'approval_pending':True,'created_at':now(),'password_hash':pwd.hash(data.password)}
    db.users.insert_one(row); audit(None,'doctor_signup_requested',uid)
    return {'status':'pending_approval','message':'Request submitted. An administrator must activate your account before you can sign in.'}

@app.post('/api/admin-register')
def admin_register(data:AdminSignup,response:Response):
    email=data.email.lower().strip()
    if '@' not in email: raise HTTPException(400,'Enter a valid email address.')
    if db.users.find_one({'email':email}): raise HTTPException(409,'An account with this email already exists.')
    hid=uuid.uuid4().hex
    hospital={'id':hid,'name':data.hospital_name.strip(),'location':data.location.strip(),'phone':data.phone.strip(),'description':data.description.strip(),'services':[],'active':True,'created_at':now()}
    row={'id':uuid.uuid4().hex,'name':data.name.strip(),'email':email,'role':'admin','hospital_id':hid,'active':True,'created_at':now(),'password_hash':pwd.hash(data.password)}
    db.hospitals.insert_one(hospital); db.users.insert_one(row); login_user(row,response); audit(row,'hospital_created',hid)
    return public(row)

@app.get('/api/hospitals')
def hospitals():
    return [hospital_public(h) for h in db.hospitals.find({'active':True})]

@app.get('/api/hospitals/{hospital_id}/doctors')
def hospital_doctors(hospital_id:str,u=Depends(require('patient'))):
    if not db.hospitals.find_one({'id':hospital_id,'active':True}): raise HTTPException(404,'Hospital not found.')
    return [public(d) for d in db.users.find({'role':'doctor','hospital_id':hospital_id,'active':True})]

@app.post('/api/appointments')
def book_appointment(data:AppointmentCreate,u=Depends(require('patient'))):
    hospital=db.hospitals.find_one({'id':data.hospital_id,'active':True})
    doctor=db.users.find_one({'id':data.doctor_id,'role':'doctor','hospital_id':data.hospital_id,'active':True})
    if not hospital: raise HTTPException(404,'Hospital not found.')
    if not doctor: raise HTTPException(404,'Choose an active doctor from this hospital.')
    scheduled=data.scheduled_at if data.scheduled_at.tzinfo else data.scheduled_at.replace(tzinfo=timezone.utc)
    if scheduled < now(): raise HTTPException(400,'Choose a future appointment time.')
    if db.appointments.find_one({'doctor_id':doctor['id'],'scheduled_at':scheduled,'status':{'$in':['booked','in_progress']}}): raise HTTPException(409,'That appointment time is already booked. Choose another time.')
    appointment_id=uuid.uuid4().hex; encounter_id=uuid.uuid4().hex
    appointment={'id':appointment_id,'encounter_id':encounter_id,'patient_id':u['id'],'doctor_id':doctor['id'],'hospital_id':hospital['id'],'scheduled_at':scheduled,'reason':data.reason.strip(),'status':'booked','created_at':now()}
    triage=calculate_triage({'symptoms':data.reason.strip(),'medical_history':''},{})
    encounter={'id':encounter_id,'appointment_id':appointment_id,'patient_id':u['id'],'doctor_id':doctor['id'],'hospital_id':hospital['id'],'token':'A-'+appointment_id[:6].upper(),'symptoms':data.reason.strip(),'age':None,'allergies':'','history':'','urgency':'routine','triage_result':triage,'status':'scheduled','created_at':now()}
    db.appointments.insert_one(appointment); db.encounters.insert_one(encounter); audit(u,'appointment_booked',appointment_id)
    return {**{k:v for k,v in appointment.items() if k!='_id'},'hospital_name':hospital['name'],'doctor_name':doctor['name']}

@app.get('/api/appointments/mine')
def my_appointments(u=Depends(require('patient'))):
    rows=list(db.appointments.find({'patient_id':u['id']}).sort('scheduled_at',1)); result=[]
    for row in rows:
        hospital=db.hospitals.find_one({'id':row['hospital_id']}) or {}; doctor=db.users.find_one({'id':row['doctor_id']}) or {}
        result.append({**{k:v for k,v in row.items() if k!='_id'},'hospital_name':hospital.get('name','Hospital'),'doctor_name':doctor.get('name','Doctor')})
    return result

@app.patch('/api/appointments/{appointment_id}/cancel')
def cancel_appointment(appointment_id:str,u=Depends(require('patient'))):
    appointment=db.appointments.find_one({'id':appointment_id,'patient_id':u['id'],'status':'booked'})
    if not appointment: raise HTTPException(404,'Bookable appointment not found.')
    db.appointments.update_one({'id':appointment_id},{'$set':{'status':'cancelled'}})
    db.encounters.update_one({'id':appointment['encounter_id'],'patient_id':u['id']},{'$set':{'status':'cancelled'}})
    audit(u,'appointment_cancelled',appointment_id)
    return {'ok':True,'status':'cancelled'}

@app.get('/api/doctor/appointments')
def doctor_appointments(u=Depends(require('doctor'))):
    rows=list(db.appointments.find({'doctor_id':u['id'],'hospital_id':u['hospital_id']}).sort('scheduled_at',1)); result=[]
    for row in rows:
        patient=db.users.find_one({'id':row['patient_id']}) or {}
        result.append({**{k:v for k,v in row.items() if k!='_id'},'patient_name':patient.get('name','Patient'),'patient_email':patient.get('email','')})
    return result

@app.get('/api/doctor/patients/{patient_id}')
def doctor_patient(patient_id:str,u=Depends(require('doctor'))):
    assigned=db.appointments.find_one({'patient_id':patient_id,'doctor_id':u['id'],'hospital_id':u['hospital_id']})
    if not assigned: raise HTTPException(404,'Patient not found in your appointments.')
    patient=db.patients.find_one({'id':patient_id}) or db.users.find_one({'id':patient_id})
    records=list(db.encounters.find({'patient_id':patient_id,'hospital_id':u['hospital_id'],'doctor_id':u['id'],'released':True}))
    return {'patient':public(patient) if patient else {'id':patient_id},'reports':[{k:v for k,v in row.items() if k!='_id'} for row in records]}

def login_user(u,response):
    token=uuid.uuid4().hex+uuid.uuid4().hex; db.sessions.insert_one({'token':token,'user_id':u['id'],'expires':now()+timedelta(hours=10)})
    response.set_cookie('mk_session',token,httponly=True,samesite='lax',secure=os.getenv('COOKIE_SECURE','false').lower()=='true',max_age=36000)

@app.post('/api/login')
def login(data:LoginCredentials,response:Response):
    u=db.users.find_one({'email':data.email.lower().strip()})
    if not u or not pwd.verify(data.password,u['password_hash']): raise HTTPException(401,'Email or password is incorrect.')
    if u.get('active') is False:
        detail='Doctor account is pending administrator approval.' if u.get('approval_pending') else 'This account is inactive.'
        raise HTTPException(403,detail)
    if data.role and u.get('role') != data.role: raise HTTPException(403,f'This account is not registered for {data.role} sign-in.')
    login_user(u,response); audit(public(u),'login'); return public(u)
@app.post('/api/logout')
def logout(request:Request,response:Response):
    db.sessions.delete_one({'token':request.cookies.get('mk_session')}); response.delete_cookie('mk_session'); return {'ok':True}
@app.get('/api/me')
def me(u=Depends(current_user)): return u

@app.post('/api/intake')
def intake(data:Intake,u=Depends(require('patient'))):
    patient_record(u['id'],u); encounter=uuid.uuid4().hex
    token=db.counters.find_one_and_update({'_id':'queue'}, {'$inc':{'n':1}}, upsert=True, return_document=ReturnDocument.AFTER)['n']
    triage=calculate_triage({'symptoms':data.symptoms,'medical_history':data.history},{})
    doc={'id':encounter,'patient_id':u['id'],'token':f'Q-{token:03d}','symptoms':data.symptoms,'age':data.age,'allergies':data.allergies,'history':data.history,'urgency':data.urgency if data.urgency in ('routine','priority') else 'routine','triage_result':triage,'status':'waiting','created_at':now()}
    db.encounters.insert_one(doc); audit(u,'intake_created',encounter); return {k:v for k,v in doc.items() if k!='_id'}
@app.get('/api/queue')
def queue(u=Depends(require('doctor'))):
    items=list(db.encounters.find({'hospital_id':u.get('hospital_id'),'doctor_id':u['id'],'status':{'$in':['waiting','in_progress']}}))
    items=sort_patients(items)
    out=[]
    for x in items:
        p=db.patients.find_one({'id':x['patient_id']}) or {}
        out.append({**{k:v for k,v in x.items() if k!='_id'},'patient_name':p.get('name','Patient')})
    return out
@app.get('/api/encounters')
def encounters(u=Depends(current_user)):
    if u['role']=='admin': raise HTTPException(403,'Administrator accounts do not have medical record access.')
    rows=list(db.encounters.find({'patient_id':u['id']})) if u['role']=='patient' else list(db.encounters.find({'hospital_id':u.get('hospital_id'),'doctor_id':u['id']}))
    if u['role']=='patient':
        return [{k:x[k] for k in ('id','appointment_id','patient_id','token','symptoms','status','released','created_at','released_at') if k in x} for x in rows]
    return [{k:v for k,v in x.items() if k!='_id'} for x in rows]
@app.get('/api/encounters/{eid}')
def get_encounter(eid:str,u=Depends(current_user)):
    if u['role']=='admin': raise HTTPException(403,'Administrator accounts do not have medical record access.')
    x=db.encounters.find_one({'id':eid})
    if not x: raise HTTPException(404,'Encounter not found.')
    if u['role']=='patient' and (x['patient_id']!=u['id'] or not x.get('released')): raise HTTPException(404,'Encounter not found.')
    if u['role']=='doctor' and not doctor_access(u,x): raise HTTPException(404,'Encounter not found.')
    return {k:v for k,v in x.items() if k!='_id'}
@app.post('/api/encounters/{eid}/documents')
async def upload(eid:str,file:UploadFile=File(...),u=Depends(require('patient'))):
    x=db.encounters.find_one({'id':eid,'patient_id':u['id']})
    if not x: raise HTTPException(404,'Encounter not found.')
    if x.get('released'): raise HTTPException(409,'This signed report is locked; contact your care team to add a correction.')
    if file.content_type not in ('image/png','image/jpeg','application/pdf'): raise HTTPException(415,'Upload a PNG, JPEG, or PDF document.')
    content=await file.read(8*1024*1024+1)
    if len(content)>8*1024*1024: raise HTTPException(413,'File exceeds the 8 MB limit.')
    ident=uuid.uuid4().hex; path=UPLOAD_DIR/ident; path.write_bytes(content); text=''; state='extracted'
    try:
        if file.content_type=='application/pdf':
            import fitz
            pdf=fitz.open(stream=content,filetype='pdf'); text='\n'.join(page.get_text() or pytesseract.image_to_string(Image.open(io.BytesIO(page.get_pixmap().tobytes('png')))) for page in pdf)
        else: text=extract_text_from_image(content)
        if text.startswith('[OCR') or text.startswith('[OCR UNAVAILABLE]'):
            text=''; state='unavailable'
    except Exception:
        state='unavailable'; text=''
    extracted=regex_extract_information(text)
    row={'id':ident,'encounter_id':eid,'patient_id':u['id'],'filename':file.filename or 'document','content_type':file.content_type,'text':text[:20000],'extracted_values':extracted,'status':state,'verified':False,'created_at':now()}; db.medical_documents.insert_one(row)
    if extracted:
        triage=calculate_triage({'symptoms':x.get('symptoms',''),'medical_history':x.get('history','')},extracted)
        db.encounters.update_one({'id':eid},{'$set':{'triage_result':triage}})
    audit(u,'document_uploaded',eid)
    return {k:v for k,v in row.items() if k!='_id'}
@app.get('/api/encounters/{eid}/documents')
def documents(eid:str,u=Depends(current_user)):
    if u['role']=='admin': raise HTTPException(403,'Administrator accounts do not have medical record access.')
    x=db.encounters.find_one({'id':eid})
    if not x or (u['role']=='patient' and x['patient_id']!=u['id']) or (u['role']=='doctor' and not doctor_access(u,x)): raise HTTPException(404,'Encounter not found.')
    rows=list(db.medical_documents.find({'encounter_id':eid})); return [{k:v for k,v in d.items() if k!='_id'} for d in rows]
@app.get('/api/documents/{doc_id}/file')
def document_file(doc_id:str,u=Depends(current_user)):
    if u['role']=='admin': raise HTTPException(403,'Administrator accounts do not have medical record access.')
    doc=db.medical_documents.find_one({'id':doc_id})
    if not doc: raise HTTPException(404,'Document not found.')
    encounter=db.encounters.find_one({'id':doc['encounter_id']})
    if not encounter or (u['role']=='patient' and doc['patient_id']!=u['id']) or (u['role']=='doctor' and not doctor_access(u,encounter)):
        raise HTTPException(404,'Document not found.')
    path=(UPLOAD_DIR / doc_id).resolve()
    if path.parent != UPLOAD_DIR.resolve() or not path.is_file(): raise HTTPException(404,'Uploaded file is no longer available.')
    filename=''.join(ch for ch in Path(doc.get('filename') or 'document').name if ch.isascii() and (ch.isalnum() or ch in ' ._-()')) or 'document'
    return Response(path.read_bytes(),media_type=doc.get('content_type') or 'application/octet-stream',headers={'Content-Disposition':f'inline; filename="{filename}"','X-Content-Type-Options':'nosniff'})
@app.patch('/api/documents/{doc_id}')
def review_document(doc_id:str,data:DocumentReview,u=Depends(require('doctor'))):
    d=db.medical_documents.find_one({'id':doc_id})
    if not d: raise HTTPException(404,'Document not found.')
    encounter=db.encounters.find_one({'id':d['encounter_id']})
    if not encounter or not doctor_access(u,encounter): raise HTTPException(404,'Document not found.')
    if encounter.get('released'): raise HTTPException(409,'This signed report is locked and its documents cannot be changed.')
    changes={'verified':data.verified}
    if data.text is not None: changes['text']=data.text[:20000]
    db.medical_documents.update_one({'id':doc_id},{'$set':changes}); audit(u,'document_reviewed',d['encounter_id']); return {'ok':True}
@app.post('/api/encounters/{eid}/soap')
def save_soap(eid:str,data:SOAP,u=Depends(require('doctor'))):
    x=db.encounters.find_one({'id':eid});
    if not x: raise HTTPException(404,'Encounter not found.')
    if not doctor_access(u,x): raise HTTPException(404,'Encounter not found.')
    if x.get('released'): raise HTTPException(409,'This signed report is locked and cannot be edited.')
    db.encounters.update_one({'id':eid},{'$set':{'soap':data.model_dump(),'status':'in_progress','doctor_id':u['id']}}); db.appointments.update_one({'encounter_id':eid,'doctor_id':u['id']},{'$set':{'status':'in_progress'}}); audit(u,'soap_saved',eid); return {'ok':True}
@app.post('/api/encounters/{eid}/prescriptions')
def prescription(eid:str,data:Prescription,u=Depends(require('doctor'))):
    x=db.encounters.find_one({'id':eid});
    if not x: raise HTTPException(404,'Encounter not found.')
    if not doctor_access(u,x): raise HTTPException(404,'Encounter not found.')
    if x.get('released'): raise HTTPException(409,'This signed report is locked and cannot be edited.')
    row={'id':uuid.uuid4().hex,'encounter_id':eid,'patient_id':x['patient_id'],'hospital_id':x.get('hospital_id'),'doctor_id':u['id'],**data.model_dump(),'released':False,'created_at':now()}; db.prescriptions.insert_one(row); audit(u,'prescription_added',eid); return {k:v for k,v in row.items() if k!='_id'}
@app.post('/api/encounters/{eid}/measurements')
def measurement(eid:str,data:Measurement,u=Depends(require('doctor'))):
    x=db.encounters.find_one({'id':eid});
    if not x: raise HTTPException(404,'Encounter not found.')
    if not doctor_access(u,x): raise HTTPException(404,'Encounter not found.')
    if x.get('released'): raise HTTPException(409,'This signed report is locked and cannot be edited.')
    row={'id':uuid.uuid4().hex,'encounter_id':eid,'patient_id':x['patient_id'],'hospital_id':x.get('hospital_id'),'doctor_id':u['id'],**data.model_dump(),'released':False,'created_at':now()}; db.measurements.insert_one(row); audit(u,'measurement_added',eid); return {'ok':True}
@app.post('/api/encounters/{eid}/release')
def release(eid:str,data:ReleaseConfirmation,u=Depends(require('doctor'))):
    x=db.encounters.find_one({'id':eid});
    if not x: raise HTTPException(404,'Encounter not found.')
    if not doctor_access(u,x): raise HTTPException(404,'Encounter not found.')
    if x.get('released'): raise HTTPException(409,'This report has already been released and signed.')
    appointment=db.appointments.find_one({'encounter_id':eid,'doctor_id':u['id'],'hospital_id':u.get('hospital_id')})
    if not appointment or appointment.get('status')!='in_progress': raise HTTPException(409,'Start the assigned appointment before signing and releasing its report.')
    if not data.confirm_signature: raise HTTPException(400,'Confirm the report before applying your digital signature.')
    if not (x.get('soap') or {}).get('summary','').strip(): raise HTTPException(400,'Add a patient summary before releasing the report.')
    signed_at=now()
    signature={'name':u['name'],'email':u['email'],'method':'authenticated_doctor_account','signed_at':signed_at}
    db.encounters.update_one({'id':eid},{'$set':{'released':True,'status':'completed','released_at':signed_at,'signature':signature}}); db.appointments.update_one({'encounter_id':eid,'doctor_id':u['id']},{'$set':{'status':'completed'}}); db.prescriptions.update_many({'encounter_id':eid},{'$set':{'released':True}}); db.measurements.update_many({'encounter_id':eid},{'$set':{'released':True}}); audit(u,'report_released',eid); return {'ok':True,'signature':signature}
def patient_rows(collection,u):
    if u['role']=='admin': raise HTTPException(403,'Administrator accounts do not have medical record access.')
    q={'patient_id':u['id']}
    if u['role']=='patient': q['released']=True
    elif u['role']=='doctor': q={'hospital_id':u.get('hospital_id'),'doctor_id':u['id']}
    return [{k:v for k,v in x.items() if k!='_id'} for x in collection.find(q)]
@app.get('/api/reports')
def reports(u=Depends(current_user)):
    if u['role']=='admin': raise HTTPException(403,'Administrator accounts do not have medical record access.')
    q={'patient_id':u['id'],'released':True} if u['role']=='patient' else {'hospital_id':u.get('hospital_id'),'doctor_id':u['id'],'released':True}
    rows=list(db.encounters.find(q)); return [{k:v for k,v in x.items() if k!='_id'} for x in rows]
@app.get('/api/prescriptions')
def prescriptions(u=Depends(current_user)): return patient_rows(db.prescriptions,u)
@app.get('/api/measurements')
def measurements(u=Depends(current_user)): return patient_rows(db.measurements,u)
@app.get('/api/contacts')
def contacts(u=Depends(current_user)):
    query={'$or':[{'hospital_id':u.get('hospital_id')},{'hospital_id':{'$exists':False}}]} if u.get('hospital_id') else {'hospital_id':{'$exists':False}}
    return [{k:v for k,v in x.items() if k!='_id'} for x in db.contacts.find(query)]
@app.post('/api/chat')
def chat(data:Chat,u=Depends(require('patient'))):
    msg=data.message.lower(); answer='I can explain common terms in released records and help you find MediKiosk features. For medical decisions, contact a qualified clinician. For urgent concerns, contact local emergency services.'
    terms={'hypertension':'Hypertension means persistently high blood pressure. A clinician can explain what it means for you.','benign':'Benign generally means not cancerous, though your clinician should explain the finding in context.','anemia':'Anemia means the blood has lower than expected oxygen-carrying red cells or hemoglobin; ask your clinician about your result.'}
    matched=False
    for term,meaning in terms.items():
        if term in msg:
            answer=meaning+' This is general information, not a diagnosis.'; matched=True; break
    if not matched and ('report' in msg or 'summary' in msg):
        rows=list(db.encounters.find({'patient_id':u['id'],'released':True})); snippets=[(r.get('soap') or {}).get('summary') for r in rows if (r.get('soap') or {}).get('summary')]
        answer='Your released summary: '+' '.join(snippets) if snippets else 'There are no released consultation summaries available yet.'
    db.chat_sessions.insert_one({'patient_id':u['id'],'question':data.message[:1000],'answer':answer,'created_at':now()}); return {'answer':answer,'provider':'rule-based'}

@app.patch('/api/doctor/appointments/{appointment_id}/status')
def update_appointment(appointment_id:str,data:AppointmentStatus,u=Depends(require('doctor'))):
    if data.status not in ('in_progress','cancelled'):
        raise HTTPException(400,'Choose an allowed appointment status.')
    appointment=db.appointments.find_one({'id':appointment_id,'doctor_id':u['id'],'hospital_id':u.get('hospital_id')})
    if not appointment: raise HTTPException(404,'Appointment not found.')
    if appointment.get('status') in ('completed','cancelled'):
        raise HTTPException(409,'Completed or cancelled appointments cannot be reopened or changed.')
    db.appointments.update_one({'id':appointment_id},{'$set':{'status':data.status}})
    db.encounters.update_one({'id':appointment['encounter_id']},{'$set':{'status':data.status}})
    audit(u,'appointment_status_changed',appointment_id)
    return {'ok':True,'status':data.status}

@app.get('/api/admin/activity')
def hospital_activity(u=Depends(require('admin'))):
    rows=list(db.appointments.find({'hospital_id':u.get('hospital_id')}).sort('created_at',-1).limit(200)); activity=[]
    for row in rows:
        patient=db.users.find_one({'id':row['patient_id']}) or {}; doctor=db.users.find_one({'id':row['doctor_id']}) or {}
        activity.append({'id':row['id'],'patient_id':row['patient_id'],'doctor_id':row['doctor_id'],'patient_name':patient.get('name','Patient'),'doctor_name':doctor.get('name','Doctor'),'scheduled_at':row.get('scheduled_at'),'status':row.get('status'),'created_at':row.get('created_at')})
    return activity

@app.get('/api/messages')
def messages(u=Depends(current_user)):
    rows=list(db.messages.find({'$or':[{'sender_id':u['id']},{'recipient_id':u['id']}]}).sort('created_at',1).limit(200))
    return [{k:v for k,v in row.items() if k!='_id'} for row in rows]

@app.post('/api/messages')
def send_message(data:MessageCreate,u=Depends(current_user)):
    if u['role']=='patient':
        allowed=db.appointments.find_one({'patient_id':u['id'],'doctor_id':data.recipient_id})
        recipient=db.users.find_one({'id':data.recipient_id,'role':'doctor'})
        if not allowed or not recipient: raise HTTPException(404,'Recipient is not part of your care team.')
    elif u['role']=='doctor':
        allowed=db.appointments.find_one({'doctor_id':u['id'],'patient_id':data.recipient_id,'hospital_id':u.get('hospital_id')})
        recipient=db.users.find_one({'id':data.recipient_id,'role':'patient'})
        if not allowed or not recipient: raise HTTPException(404,'Recipient is not one of your patients.')
    else:
        recipient=db.users.find_one({'id':data.recipient_id,'role':'doctor','hospital_id':u.get('hospital_id')})
        if not recipient:
            recipient=db.users.find_one({'id':data.recipient_id,'role':'patient'})
            assigned=db.appointments.find_one({'patient_id':data.recipient_id,'hospital_id':u.get('hospital_id')})
            if not assigned: recipient=None
        if not recipient: raise HTTPException(404,'Recipient not found in your hospital.')
    row={'id':uuid.uuid4().hex,'hospital_id':u.get('hospital_id') or recipient.get('hospital_id'),'sender_id':u['id'],'sender_name':u['name'],'recipient_id':recipient['id'],'recipient_name':recipient['name'],'content':data.content.strip(),'created_at':now()}
    db.messages.insert_one(row); audit(u,'message_sent',recipient['id'])
    return {k:v for k,v in row.items() if k!='_id'}

@app.get('/api/admin/doctors')
def doctors(u=Depends(require('admin'))): return [public(x) for x in db.users.find({'role':'doctor','hospital_id':u.get('hospital_id')})]
@app.post('/api/admin/doctors')
def add_doctor(data:DoctorCreate,u=Depends(require('admin'))):
    email=data.email.lower().strip()
    if db.users.find_one({'email':email}): raise HTTPException(409,'Email already exists.')
    row={'id':uuid.uuid4().hex,'name':data.name,'email':email,'role':'doctor','hospital_id':u.get('hospital_id'),'active':True,'created_at':now(),'password_hash':pwd.hash(data.password)}; db.users.insert_one(row); audit(u,'doctor_created',row['id']); return public(row)
@app.patch('/api/admin/doctors/{uid}/toggle')
def toggle(uid:str,u=Depends(require('admin'))):
    d=db.users.find_one({'id':uid,'role':'doctor','hospital_id':u.get('hospital_id')});
    if not d: raise HTTPException(404,'Doctor not found.')
    active=not d.get('active',True)
    db.users.update_one({'id':uid},{'$set':{'active':active,'approval_pending':False}}); audit(u,'doctor_status_changed',uid); return {'active':active}
@app.post('/api/admin/contacts')
def add_contact(data:Contact,u=Depends(require('admin'))):
    row={'id':uuid.uuid4().hex,'hospital_id':u.get('hospital_id'),**data.model_dump(),'sample':True}; db.contacts.insert_one(row); audit(u,'contact_created',row['id']); return {k:v for k,v in row.items() if k!='_id'}
@app.get('/api/admin/stats')
def stats(u=Depends(require('admin'))):
    hospital_id=u.get('hospital_id')
    return {'patients':len(db.appointments.distinct('patient_id',{'hospital_id':hospital_id})),'doctors':db.users.count_documents({'role':'doctor','hospital_id':hospital_id}),'waiting':db.appointments.count_documents({'hospital_id':hospital_id,'status':'booked'}),'completed':db.appointments.count_documents({'hospital_id':hospital_id,'status':'completed'})}
@app.get('/api/admin/audit')
def audit_events(u=Depends(require('admin'))): return [{k:v for k,v in x.items() if k!='_id'} for x in db.audit_events.find({'hospital_id':u.get('hospital_id')}).sort('at',-1).limit(100)]

