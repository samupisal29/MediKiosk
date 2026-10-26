import heapq
from datetime import datetime

def sort_patients(patients: list) -> list:
    """
    Sorts a list of patient dictionaries using heapq (min-heap).
    Priority 1 = Highest urgency, 5 = Lowest urgency.
    For equal priorities, earlier creation time gets priority (FIFO).
    """
    heap = []
    count = 0  # tiebreaker for equal priorities & timestamps

    for patient in patients:
        priority = 5
        triage = patient.get("triage_result")
        if triage:
            try:
                priority = int(triage.get("priority_score", 5))
            except (TypeError, ValueError):
                priority = 5

        created_at = patient.get("created_at")
        # Normalize created_at to a comparable value
        if isinstance(created_at, datetime):
            ts = created_at.timestamp()
        elif isinstance(created_at, str):
            try:
                ts = datetime.fromisoformat(created_at).timestamp()
            except (ValueError, TypeError):
                ts = 0.0
        else:
            ts = 0.0

        # Push (priority_score, timestamp, insertion_order, patient)
        heapq.heappush(heap, (priority, ts, count, patient))
        count += 1

    ordered_patients = []
    while heap:
        _, _, _, patient = heapq.heappop(heap)
        ordered_patients.append(patient)

    return ordered_patients
