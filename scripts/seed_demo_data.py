"""Seed demo users/patients/documents for local development and the curl
tour in the README. Not used by the test suite (tests build their own
fixtures per-test via testcontainers) -- this is purely for `docker compose
up` to produce something to click around.

    python scripts/seed_demo_data.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db.session import Database
from app.models.care_assignment import CareAssignment
from app.models.document import Document
from app.models.patient import Patient
from app.models.user import User
from app.security.passwords import hash_password
from app.services.embeddings import HashingEmbedder

DEMO_USERS = [
    ("clinician.a@example.org", "clinician-a-pass", "Dr. Alicia Ramirez", "clinician"),
    ("clinician.b@example.org", "clinician-b-pass", "Dr. Ben Okafor", "clinician"),
    ("auditor@example.org", "auditor-pass", "Priya Auditor", "auditor"),
    ("admin@example.org", "admin-pass", "Sam Admin", "admin"),
]

DEMO_PATIENTS = [
    ("Shantelle354", "Shantelle354", "Rolfson603", "1988-04-12", "female"),
    ("Marcus812", "Marcus812", "Weimann271", "1975-11-02", "male"),
]

DEMO_NOTES = {
    "Shantelle354": [
        ("2019-06-14", "ambulatory", "Patient presents for a wellness visit. Metformin 500mg prescribed for type 2 diabetes management."),
        ("2020-01-22", "ambulatory", "Follow-up visit. Blood pressure well controlled on lisinopril 10mg daily."),
        ("2021-08-03", "ambulatory", "Diabetes follow-up. A1c improved to 6.8%. Continue metformin 500mg, reinforced diet and exercise counseling."),
    ],
    "Marcus812": [
        ("2018-03-09", "emergency", "Patient presented to the emergency department with chest pain, ruled out for MI."),
        ("2021-09-30", "ambulatory", "Annual physical. No new complaints. Continues atorvastatin 20mg for hyperlipidemia."),
        ("2022-11-14", "ambulatory", "Lipid panel reviewed. LDL at goal on atorvastatin 20mg daily. No new symptoms reported."),
    ],
}


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    embedder = HashingEmbedder(dim=settings.embedding_dim)

    async with db.session_unscoped() as session:
        users = {}
        for email, password, name, role in DEMO_USERS:
            user = User(id=uuid.uuid4(), email=email, hashed_password=hash_password(password), full_name=name, role=role)
            session.add(user)
            users[email] = user
        await session.flush()

        patients = {}
        for external_id, given, family, dob, gender in DEMO_PATIENTS:
            from datetime import date

            patient = Patient(
                id=uuid.uuid4(),
                external_id=external_id,
                given_name=given,
                family_name=family,
                birth_date=date.fromisoformat(dob),
                gender=gender,
            )
            session.add(patient)
            patients[external_id] = patient
        await session.flush()

        session.add(
            CareAssignment(
                id=uuid.uuid4(),
                clinician_id=users["clinician.a@example.org"].id,
                patient_id=patients["Shantelle354"].id,
            )
        )
        session.add(
            CareAssignment(
                id=uuid.uuid4(),
                clinician_id=users["clinician.b@example.org"].id,
                patient_id=patients["Marcus812"].id,
            )
        )
        await session.flush()

        print("Seeded users:")
        for email, _, _, role in DEMO_USERS:
            print(f"  {email}  role={role}")
        print("Seeded patients:")
        for external_id, *_ in DEMO_PATIENTS:
            print(f"  {external_id}  id={patients[external_id].id}")

    async with db.session_as(str(uuid.UUID(int=0)), "service") as session:
        for i, (external_id, notes) in enumerate(DEMO_NOTES.items()):
            patient = patients[external_id]
            for j, (enc_date, enc_type, text) in enumerate(notes):
                doc = Document(
                    id=uuid.uuid4(),
                    patient_id=patient.id,
                    chunk_id=f"fixed_512::demo-{external_id}::{j}",
                    chunk_level="fixed_512",
                    encounter_date=enc_date,
                    encounter_type=enc_type,
                    text=text,
                    embedding=embedder.embed(text),
                )
                session.add(doc)

    await db.dispose()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
