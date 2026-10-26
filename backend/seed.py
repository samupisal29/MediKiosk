"""Seed deterministic fictional demo accounts and sample records."""
from demo_data import DEMO_PASSWORD, seed_demo_data
from main import DATABASE_MODE, db, now, pwd

seed_demo_data(db, pwd, now)

print(f'Demo data ready (database: {DATABASE_MODE}).')
print('All demo users use the password: ' + DEMO_PASSWORD)
print('Demo accounts: admin@medikiosk.demo, doctor@medikiosk.demo, patient1@medikiosk.demo, patient2@medikiosk.demo')
