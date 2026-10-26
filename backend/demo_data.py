"""Deterministic fictional accounts and records for local demonstrations."""
from datetime import timedelta


DEMO_PASSWORD = 'DemoPass123!'
HOSPITAL_ID = 'demo-hospital'


def seed_demo_data(db, password_hasher, now):
    hospital = {
        'id': HOSPITAL_ID,
        'name': 'MediKiosk Demo Hospital',
        'location': 'Demo City',
        'phone': 'Sample contact only',
        'description': 'A fictional hospital for testing the MediKiosk workflow.',
        'services': ['General medicine'],
        'active': True,
        'created_at': now(),
    }
    db.hospitals.update_one({'id': HOSPITAL_ID}, {'$setOnInsert': hospital}, upsert=True)

    accounts = [
        ('demo-admin', 'Demo Administrator', 'admin@medikiosk.demo', 'admin', HOSPITAL_ID),
        ('demo-doctor', 'Demo Doctor', 'doctor@medikiosk.demo', 'doctor', HOSPITAL_ID),
        ('demo-patient-1', 'Alex Example', 'patient1@medikiosk.demo', 'patient', None),
        ('demo-patient-2', 'Sam Sample', 'patient2@medikiosk.demo', 'patient', None),
    ]
    for uid, name, email, role, hospital_id in accounts:
        record = {
            'id': uid,
            'name': name,
            'email': email,
            'role': role,
            'active': True,
            'password_hash': password_hasher.hash(DEMO_PASSWORD),
            'created_at': now(),
        }
        if hospital_id:
            record['hospital_id'] = hospital_id
        db.users.update_one(
            {'email': email},
            {'$set': record},
            upsert=True,
        )
        if role == 'patient':
            db.patients.update_one(
                {'id': uid},
                {'$setOnInsert': {'id': uid, 'name': name, 'user_id': uid, 'created_at': now(), 'fictional': True}},
                upsert=True,
            )

    if db.encounters.count_documents({'fictional': True}) == 0:
        db.encounters.insert_many([
            {
                'id': 'demo-encounter-released', 'patient_id': 'demo-patient-1',
                'doctor_id': 'demo-doctor', 'hospital_id': HOSPITAL_ID, 'token': 'DEMO-01',
                'symptoms': 'Fictional example: seasonal check-in', 'age': 34,
                'allergies': 'Not reported', 'history': 'Fictional sample record',
                'urgency': 'routine', 'status': 'completed', 'released': True,
                'soap': {'subjective': 'Fictional example only.', 'objective': 'Not clinically assessed.',
                         'assessment': 'Demonstration record.', 'plan': 'Contact a clinician for care.',
                         'summary': 'This is a fictional demonstration summary.'},
                'signature': {'name': 'Demo Doctor', 'email': 'doctor@medikiosk.demo',
                              'method': 'authenticated_doctor_account', 'signed_at': now()},
                'created_at': now(), 'released_at': now(), 'fictional': True,
            },
            {
                'id': 'demo-encounter-unreleased', 'patient_id': 'demo-patient-2',
                'doctor_id': 'demo-doctor', 'hospital_id': HOSPITAL_ID, 'token': 'DEMO-02',
                'symptoms': 'Fictional example: queue review', 'age': 28,
                'allergies': 'Not reported', 'history': 'Fictional sample record',
                'urgency': 'priority', 'status': 'waiting', 'created_at': now(), 'fictional': True,
            },
        ])
        db.prescriptions.insert_one({
            'id': 'demo-prescription', 'patient_id': 'demo-patient-1', 'doctor_id': 'demo-doctor',
            'hospital_id': HOSPITAL_ID, 'encounter_id': 'demo-encounter-released',
            'name': 'Fictional sample item', 'instructions': 'Not a real prescription.',
            'released': True, 'created_at': now(), 'fictional': True,
        })
        db.measurements.insert_one({
            'id': 'demo-reading', 'patient_id': 'demo-patient-1', 'doctor_id': 'demo-doctor',
            'hospital_id': HOSPITAL_ID, 'encounter_id': 'demo-encounter-released',
            'label': 'Example reading', 'value': 'Fictional data',
            'released': True, 'created_at': now(), 'fictional': True,
        })

    if db.appointments.count_documents({'fictional': True}) == 0:
        db.appointments.insert_many([
            {
                'id': 'demo-appointment-completed', 'encounter_id': 'demo-encounter-released',
                'patient_id': 'demo-patient-1', 'doctor_id': 'demo-doctor', 'hospital_id': HOSPITAL_ID,
                'scheduled_at': now() - timedelta(days=7), 'reason': 'Fictional routine consultation',
                'status': 'completed', 'created_at': now() - timedelta(days=7), 'fictional': True,
            },
            {
                'id': 'demo-appointment-upcoming', 'encounter_id': 'demo-encounter-unreleased',
                'patient_id': 'demo-patient-2', 'doctor_id': 'demo-doctor', 'hospital_id': HOSPITAL_ID,
                'scheduled_at': now() + timedelta(days=1), 'reason': 'Fictional follow-up visit',
                'status': 'booked', 'created_at': now(), 'fictional': True,
            },
        ])

    if db.contacts.count_documents({'sample': True}) == 0:
        db.contacts.insert_many([
            {'id': 'sample-general', 'hospital_id': HOSPITAL_ID, 'department': 'General information',
             'phone': 'Sample contact: not a real number', 'hours': 'Weekdays, 9:00-17:00',
             'note': 'Fictional demonstration contact', 'sample': True},
            {'id': 'sample-records', 'hospital_id': HOSPITAL_ID, 'department': 'Records desk',
             'phone': 'Sample contact: not a real number', 'hours': 'Weekdays, 9:00-16:00',
             'note': 'Fictional demonstration contact', 'sample': True},
        ])
