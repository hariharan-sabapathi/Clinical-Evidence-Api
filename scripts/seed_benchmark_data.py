"""Seed a larger corpus for the k6 load test in benchmarks/ -- enough
patients and chunks-per-patient that vector search cost is non-trivial
(the whole point of the before/after HNSW-index comparison) and enough
distinct clinician/patient pairs that 50 concurrent virtual users aren't
all serializing through one patient's rows.

    python scripts/seed_benchmark_data.py

Writes benchmarks/actors.json: [{"email", "password", "patient_id"}, ...]
for the k6 script to log in with.
"""

from __future__ import annotations

import asyncio
import json
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

NUM_PATIENTS = 60
DOCS_PER_PATIENT = 300

_SECTIONS = [
    "Chief complaint: {n} presents for a routine follow-up visit.",
    "Assessment: type 2 diabetes mellitus, well controlled on metformin {n}mg twice daily.",
    "Medications: lisinopril {n}mg daily for hypertension, atorvastatin 20mg for hyperlipidemia.",
    "Vitals: BP 12{n}/78, HR 7{n}, temp 98.{n}F, weight stable since last visit.",
    "Plan: continue current regimen, follow up in 3 months, labs ordered for HbA1c and lipid panel.",
    "History: no known drug allergies, family history of coronary artery disease.",
    "Social history: non-smoker, occasional alcohol use, exercises 3 times per week.",
    "Immunizations up to date including annual influenza vaccine administered this visit.",
]


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    embedder = HashingEmbedder(dim=settings.embedding_dim)
    actors = []

    async with db.session_unscoped() as session:
        for p in range(NUM_PATIENTS):
            clinician = User(
                id=uuid.uuid4(),
                email=f"bench.clinician.{p}@example.org",
                hashed_password=hash_password("bench-pass"),
                full_name=f"Bench Clinician {p}",
                role="clinician",
            )
            patient = Patient(
                id=uuid.uuid4(),
                external_id=f"Bench{p:03d}",
                given_name=f"Bench{p:03d}",
                family_name="Loadtest",
                gender="female" if p % 2 == 0 else "male",
            )
            session.add(clinician)
            session.add(patient)
            await session.flush()
            session.add(CareAssignment(id=uuid.uuid4(), clinician_id=clinician.id, patient_id=patient.id))
            actors.append(
                {"email": clinician.email, "password": "bench-pass", "patient_id": str(patient.id)}
            )
            print(f"patient {p + 1}/{NUM_PATIENTS}: {patient.external_id}")

    async with db.session_as(str(uuid.UUID(int=0)), "service") as session:
        for p, actor in enumerate(actors):
            patient_id = actor["patient_id"]
            for j in range(DOCS_PER_PATIENT):
                template = _SECTIONS[j % len(_SECTIONS)]
                text = template.format(n=(j % 9) + 1)
                embedding = embedder.embed(text)
                session.add(
                    Document(
                        id=uuid.uuid4(),
                        patient_id=patient_id,
                        chunk_id=f"bench::{patient_id}::{j}",
                        chunk_level="fixed_512",
                        encounter_date=f"20{10 + (j % 14):02d}-0{(j % 9) + 1}-1{j % 9}",
                        encounter_type="ambulatory",
                        text=text,
                        embedding=embedding,
                    )
                )
            if (p + 1) % 5 == 0:
                await session.flush()
                print(f"  ...seeded {DOCS_PER_PATIENT} docs for {p + 1}/{NUM_PATIENTS} patients")

    out_path = Path(__file__).resolve().parents[1] / "benchmarks" / "actors.json"
    out_path.write_text(json.dumps(actors, indent=2))
    print(f"Wrote {len(actors)} actors to {out_path}")

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
