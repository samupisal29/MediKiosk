# MediKiosk

A fictional-data hospital booking MVP. Patients browse hospitals, book appointments, upload documents, and view released reports. Doctors manage only their assigned patients at their hospital. Each hospital has its own administrator account for access approvals, appointment activity, and messaging. This is not a clinical system and makes no compliance or clinical-accuracy claims.

## Stack and layout

- `frontend/`: Vite, HTML, CSS and vanilla ES modules
- `backend/`: FastAPI, PyMongo, hospital scoped accounts and records, Tesseract OCR, messaging, and a rule-based report assistant
- MongoDB Community Edition is the persistent store; `mongomock` provides an in-memory fallback for local demonstrations.

The frontend remains the Medi-Kisok Vite application. The backend includes EDIL's rule-based triage and FIFO priority-queue modules, wired into patient intake, appointment creation, and the doctor queue. EDIL's original API only supports its standalone intake/consultation workflow; replacing the hospital, account, booking, document, and messaging API with it would break pages in this frontend. Those existing API routes are retained as the compatibility layer, while EDIL's triage logic is used for clinical prioritization.

## Prerequisites

Install Python 3.12 (recommended) and Node.js 18+. Python 3.15 may force pip to compile PyYAML locally on Windows and require Microsoft C++ Build Tools. MongoDB Community is recommended for persistent storage; when it is unavailable, the backend uses an in-memory database for local demonstrations. Data in memory is cleared when the backend restarts. Tesseract is optional and enables image OCR. PDF OCR additionally requires `pymupdf` and a working Tesseract installation.

## Setup (PowerShell)

Terminal 1, backend:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python seed.py
uvicorn main:app --reload
```

Terminal 2, frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL (normally http://localhost:5173); API docs are at http://localhost:8000/docs. The seed script creates fictional demo records without prompting. Demo accounts use the password `DemoPass123!`: `admin@medikiosk.demo`, `doctor@medikiosk.demo`, `patient1@medikiosk.demo`, and `patient2@medikiosk.demo`. These static credentials are for local demonstration only. Patients can register in the app. Doctors can request access to a hospital and must be approved by its administrator. A hospital administrator can register a new hospital from the sign-in page.

macOS/Linux equivalents: create the venv with `python3.12 -m venv .venv`, activate using `source .venv/bin/activate`, then run the same pip, seed, and uvicorn commands. In the frontend use `npm install` and `npm run dev`.

## Configuration

Review `backend/.env.example` and keep `.env` private. `MONGODB_URI` defaults to local MongoDB. If MongoDB is unavailable, `mongomock` provides an ephemeral in-memory fallback and demo users are loaded automatically; use MongoDB for data that must persist. `DEMO_MODE` defaults to `true` for local use; set it to `false` outside local demonstrations to disable automatic demo accounts in the in-memory fallback. `FRONTEND_ORIGIN` controls CORS. The Vite development server proxies `/api` to FastAPI. `COOKIE_SECURE` should be `true` when served over HTTPS. `TESSERACT_CMD` is optional when the executable is not on PATH. Login uses opaque, random, HTTP-only session cookies. Uploaded image text is extracted with Tesseract OCR and simple regex rules; no AI service is used.

## Features

Patient and doctor registration/login, hospital discovery, appointment booking, image/PDF document uploads and Tesseract OCR, doctor appointments and patient profiles scoped to assignments, clinical notes/prescriptions/measurements, explicit report release, patient access to released reports, patient-doctor-hospital administrator messaging, hospital administrator activity history, and a rule-based report assistant. All seeded records are fictional.

## Limitations

This is an educational MVP, not ready for real patient data. Before any real deployment, add security review, HTTPS, CSRF protection, key rotation, backups, malware scanning, retention/deletion controls, stronger throttling, operational monitoring, clinical governance, and jurisdiction-specific compliance. Do not use it for diagnosis or treatment. OCR text is unverified until reviewed by a doctor.

## Checks

Run API checks with `cd backend; python -m pytest test_hospital_workflow.py tests.py` and the frontend build with `cd frontend; npm run build`.
