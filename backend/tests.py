"""Small isolated API checks. Requires MongoDB and the backend dependencies."""
import os
import pytest
os.environ.setdefault('MONGODB_DATABASE','medikiosk_test')
from fastapi.testclient import TestClient
from main import app, db

client=TestClient(app)
def test_health():
    assert client.get('/api/health').status_code==200
def test_patient_register_intake_and_ownership():
    if client.get('/api/health').json().get('database') != 'connected':
        pytest.skip('MongoDB is not running; start MongoDB Community to run persistence tests.')
    email='test-'+__import__('uuid').uuid4().hex+'@example.test'
    c=TestClient(app)
    r=c.post('/api/register',json={'name':'Test Patient','email':email,'password':'not-a-real-demo-password'})
    assert r.status_code==200
    x=c.post('/api/intake',json={'symptoms':'Fictional test symptom','age':30,'urgency':'routine'})
    assert x.status_code==200 and x.json()['token'].startswith('Q-')
    assert c.get('/api/encounters').json()[0]['patient_id']==r.json()['id']
    other=TestClient(app); other.post('/api/register',json={'name':'Other Patient','email':email+'x','password':'not-a-real-demo-password'})
    assert other.get('/api/encounters/'+x.json()['id']).status_code==404
    db.users.delete_one({'email':email}); db.patients.delete_one({'id':r.json()['id']}); db.encounters.delete_one({'id':x.json()['id']})
