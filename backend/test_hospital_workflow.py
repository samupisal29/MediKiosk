"""End-to-end API checks for the hospital-scoped patient workflow."""
import os
import uuid
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

os.environ['MONGODB_DATABASE'] = 'medikiosk_test'
from main import app, db


def test_hospital_signup_booking_scoping_and_messages(monkeypatch):
    root = TestClient(app)
    if root.get('/api/health').json().get('database') != 'connected':
        pytest.skip('MongoDB is not running.')

    suffix = uuid.uuid4().hex
    admin_email = f'admin-{suffix}@example.test'
    doctor_email = f'doctor-{suffix}@example.test'
    patient_email = f'patient-{suffix}@example.test'
    other_admin_email = f'other-admin-{suffix}@example.test'
    other_doctor_email = f'other-doctor-{suffix}@example.test'
    hospital_ids = []
    user_ids = []
    try:
        admin = TestClient(app)
        response = admin.post('/api/admin-register', json={
            'name': 'Test Hospital Admin', 'email': admin_email,
            'password': 'test-admin-password-123', 'hospital_name': f'Care Center {suffix}',
            'location': 'Test City', 'phone': '000-000-0000', 'description': 'Test only',
        })
        assert response.status_code == 200, response.text
        hospital_id = response.json()['hospital_id']
        admin_id = response.json()['id']
        hospital_ids.append(hospital_id)
        user_ids.append(admin_id)

        doctor_signup = TestClient(app)
        response = doctor_signup.post('/api/doctor-register', json={
            'name': 'Test Doctor', 'email': doctor_email,
            'password': 'test-doctor-password-123', 'hospital_id': hospital_id,
        })
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'pending_approval'
        locked = doctor_signup.post('/api/login', json={'email':doctor_email,'password':'test-doctor-password-123','role':'doctor'})
        assert locked.status_code == 403 and 'pending' in locked.json()['detail'].lower()
        doctors = admin.get('/api/admin/doctors').json()
        pending = next(d for d in doctors if d['email'] == doctor_email)
        user_ids.append(pending['id'])
        assert pending['approval_pending'] is True
        assert admin.patch(f"/api/admin/doctors/{pending['id']}/toggle").json()['active'] is True

        doctor = TestClient(app)
        response = doctor.post('/api/login', json={
            'email': doctor_email, 'password': 'test-doctor-password-123', 'role': 'doctor',
        })
        assert response.status_code == 200, response.text
        assert response.json()['hospital_id'] == hospital_id

        patient = TestClient(app)
        response = patient.post('/api/register', json={
            'name': 'Test Patient', 'email': patient_email,
            'password': 'test-patient-password-123',
        })
        assert response.status_code == 200, response.text
        patient_id = response.json()['id']
        user_ids.append(patient_id)
        assert any(h['id'] == hospital_id for h in patient.get('/api/hospitals').json())
        assert patient.get(f'/api/hospitals/{hospital_id}/doctors').json()[0]['id'] == pending['id']

        appointment_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        response = patient.post('/api/appointments', json={
            'hospital_id': hospital_id, 'doctor_id': pending['id'],
            'scheduled_at': appointment_time, 'reason': 'Routine consultation',
        })
        assert response.status_code == 200, response.text
        appointment = response.json()
        assert appointment['hospital_id'] == hospital_id
        assert patient.get(f"/api/encounters/{appointment['encounter_id']}").status_code == 404

        doctor_appointments = doctor.get('/api/doctor/appointments').json()
        assert len(doctor_appointments) == 1
        assert doctor_appointments[0]['patient_id'] == patient_id
        assert doctor.get(f'/api/doctor/patients/{patient_id}').status_code == 200
        assert doctor.post(
            f"/api/encounters/{appointment['encounter_id']}/release",
            json={'confirm_signature':True},
        ).status_code == 409
        started = doctor.patch(f"/api/doctor/appointments/{appointment['id']}/status", json={'status':'in_progress'})
        assert started.status_code == 200, started.text
        image_buffer = io.BytesIO()
        Image.new('RGB', (16, 16), 'white').save(image_buffer, format='PNG')
        image_bytes = image_buffer.getvalue()
        uploaded = patient.post(
            f"/api/encounters/{appointment['encounter_id']}/documents",
            files={'file': ('sample.png', image_bytes, 'image/png')},
        )
        assert uploaded.status_code == 200, uploaded.text
        document_id = uploaded.json()['id']
        assert doctor.get(f'/api/documents/{document_id}/file').content == image_bytes
        saved = doctor.post(f"/api/encounters/{appointment['encounter_id']}/soap", json={'summary':'Fictional test summary','transcript':'Fictional dictated note'})
        assert saved.status_code == 200, saved.text
        assert doctor.get(f"/api/encounters/{appointment['encounter_id']}").json()['soap']['transcript'] == 'Fictional dictated note'
        missing_signature = doctor.post(f"/api/encounters/{appointment['encounter_id']}/release", json={'confirm_signature': False})
        assert missing_signature.status_code == 400
        released = doctor.post(f"/api/encounters/{appointment['encounter_id']}/release", json={'confirm_signature': True})
        assert released.status_code == 200, released.text
        assert released.json()['signature']['name'] == 'Test Doctor'
        assert patient.get(f"/api/encounters/{appointment['encounter_id']}").json()['released'] is True
        assert doctor.post(f"/api/encounters/{appointment['encounter_id']}/soap", json={'summary':'Changed after release'}).status_code == 409
        assert doctor.patch(f'/api/documents/{document_id}', json={'verified':True}).status_code == 409
        assert patient.post(
            f"/api/encounters/{appointment['encounter_id']}/documents",
            files={'file': ('late.png', image_bytes, 'image/png')},
        ).status_code == 409
        assert doctor.patch(
            f"/api/doctor/appointments/{appointment['id']}/status",
            json={'status':'in_progress'},
        ).status_code == 409
        assert len(patient.get('/api/reports').json()) == 1
        assert admin.get('/api/admin/activity').json()[0]['patient_id'] == patient_id
        assert 'reason' not in admin.get('/api/admin/activity').json()[0]
        assert admin.get('/api/reports').status_code == 403

        assert admin.post('/api/messages', json={
            'recipient_id': patient_id, 'content': 'Your appointment is confirmed.',
        }).status_code == 200
        assert doctor.post('/api/messages', json={
            'recipient_id': patient_id, 'content': 'Please bring your documents.',
        }).status_code == 200
        assert len(patient.get('/api/messages').json()) == 2

        other_admin = TestClient(app)
        response = other_admin.post('/api/admin-register', json={
            'name': 'Other Admin', 'email': other_admin_email,
            'password': 'test-other-admin-password', 'hospital_name': f'Other Hospital {suffix}',
        })
        assert response.status_code == 200, response.text
        other_hospital_id = response.json()['hospital_id']
        other_admin_id = response.json()['id']
        hospital_ids.append(other_hospital_id)
        user_ids.append(other_admin_id)
        other_admin.post('/api/admin/doctors', json={
            'name': 'Other Doctor', 'email': other_doctor_email,
            'password': 'test-other-doctor-password',
        }).raise_for_status()
        other_doctors = other_admin.get('/api/admin/doctors').json()
        other_doctor_id = next(d['id'] for d in other_doctors if d['email'] == other_doctor_email)
        user_ids.append(other_doctor_id)
        other_doctor = TestClient(app)
        assert other_doctor.post('/api/login', json={
            'email': other_doctor_email, 'password': 'test-other-doctor-password', 'role': 'doctor',
        }).status_code == 200
        assert other_doctor.get('/api/doctor/appointments').json() == []
        assert other_doctor.get(f'/api/doctor/patients/{patient_id}').status_code == 404
        assert other_admin.get('/api/admin/activity').json() == []
        assert other_admin.get('/api/admin/doctors').json() and all(d['hospital_id'] == other_hospital_id for d in other_admin.get('/api/admin/doctors').json())

        assert patient.post('/api/ai/patient', json={
            'question': 'Explain my released report', 'consent': True,
        }).status_code == 404
    finally:
        db.sessions.delete_many({'user_id': {'$in': user_ids}})
        for collection in ('appointments', 'encounters', 'messages', 'audit_events', 'medical_documents', 'prescriptions', 'measurements', 'chat_sessions'):
            if hospital_ids:
                db[collection].delete_many({'hospital_id': {'$in': hospital_ids}})
        db.patients.delete_many({'id': {'$in': user_ids}})
        db.users.delete_many({'$or': [{'id': {'$in': user_ids}}, {'email': {'$in': [admin_email, doctor_email, patient_email, other_admin_email, other_doctor_email]}}]})
        db.hospitals.delete_many({'id': {'$in': hospital_ids}})
