def calculate_triage(intake_data: dict, extracted_data: dict) -> dict:
    """
    Rule-based triage algorithm.

    Priority scale (deterministic, rule-based):
      1 = Critical  — life-threatening symptoms
      2 = High      — urgent symptoms or abnormal vitals
      3 = Medium    — moderate symptoms
      4 = Low       — mild symptoms
      5 = Minimal   — routine / no concerning findings

    NOTE: This is an academic prototype for demonstration purposes only.
    It does NOT constitute a certified medical triage system and must NOT
    be used for clinical decision-making.
    """
    priority = 5
    reason = "No concerning symptoms or vital signs detected."
    flags = []

    import re

    symptoms = intake_data.get("symptoms", "").lower()
    history  = intake_data.get("medical_history", "").lower()

    # Combine symptoms and acute history for comprehensive matching
    combined_text = f"{symptoms} {history}".strip()

    # ── Priority 1: Critical / life-threatening ──────────────────────────────
    critical_patterns = [
        (r"\bheart\s*attack\b", "heart attack"),
        (r"\bheartattack\b", "heart attack"),
        (r"\b(?:chest|cardiac|heart)\s*(?:pain|tightness|pressure|discomfort|heaviness)\b", "chest/cardiac pain"),
        (r"\bpain\s*in\s*(?:my\s*)?(?:chest|heart)\b", "chest/cardiac pain"),
        (r"\bcardiac\s*(?:arrest|event|failure|emergency)?\b", "cardiac emergency"),
        (r"\bmyocardial\s*infarction\b", "myocardial infarction"),
        (r"\bangina\b", "angina"),
        (r"\bstroke\b", "stroke"),
        (r"\b(?:unconscious|unresponsive|collapsed|passed\s*out)\b", "unresponsive/collapsed"),
        (r"\b(?:not\s*breathing|cannot\s*breathe|can't\s*breathe|stopped\s*breathing)\b", "not breathing"),
        (r"\b(?:severe\s*)?breathing\s*difficulty\b", "breathing difficulty"),
        (r"\b(?:severe\s*)?shortness\s*of\s*breath\b", "shortness of breath"),
        (r"\bsevere\s*bleeding\b", "severe bleeding"),
        (r"\banaphylaxis\b", "anaphylaxis"),
        (r"\bseizure\b", "seizure"),
        (r"\bchoking\b", "choking"),
        (r"\bsuicid(?:e|al)\b", "suicidal ideation")
    ]
    for pattern, label in critical_patterns:
        if re.search(pattern, combined_text):
            return {
                "priority_score": 1,
                "reason": f"Critical symptom detected: '{label}'.",
                "flags": ["CRITICAL_SYMPTOM"]
            }

    # ── Priority 2: High risk ─────────────────────────────────────────────────
    high_risk_patterns = [
        (r"\bfever\b", "fever"),
        (r"\bhigh\s*temp(?:erature)?\b", "high temperature"),
        (r"\bfracture\b", "fracture"),
        (r"\bbroken\s*bone\b", "broken bone"),
        (r"\bsevere\s*pain\b", "severe pain"),
        (r"\bhead\s*injury\b", "head injury"),
        (r"\bvomiting\s*blood\b", "vomiting blood"),
        (r"\babdominal\s*pain\b", "abdominal pain"),
        (r"\bdifficulty\s*breathing\b", "difficulty breathing")
    ]
    for pattern, label in high_risk_patterns:
        if re.search(pattern, symptoms):
            priority = 2
            reason = f"High-risk symptom detected: '{label}'."
            flags.append("HIGH_RISK")
            break

    # ── Priority 3: Moderate ──────────────────────────────────────────────────
    if priority > 3:
        moderate_keywords = ["pain", "swelling", "dizziness", "nausea", "cough", "infection"]
        for kw in moderate_keywords:
            if kw in symptoms:
                priority = 3
                reason = f"Moderate symptom detected: '{kw}'."
                flags.append("MODERATE")
                break

    # ── Vital sign checks from OCR-extracted data ─────────────────────────────
    hr_str = extracted_data.get("Heart Rate", "")
    if hr_str:
        try:
            hr_val = int(hr_str.split()[0])
            if hr_val > 120 or hr_val < 50:
                flags.append("ABNORMAL_HR")
                if priority > 2:
                    priority = 2
                    reason = f"Abnormal heart rate in uploaded report: {hr_val} bpm."
        except (ValueError, IndexError):
            pass

    bp_str = extracted_data.get("Blood Pressure", "")
    if bp_str:
        try:
            sys_bp = int(bp_str.split("/")[0])
            if sys_bp > 180 or sys_bp < 80:
                flags.append("ABNORMAL_BP")
                if priority > 2:
                    priority = 2
                    reason = f"Abnormal blood pressure in uploaded report: {bp_str}."
        except (ValueError, IndexError):
            pass

    spo2_str = extracted_data.get("SpO2", "")
    if spo2_str:
        try:
            spo2_val = int(spo2_str.replace("%", "").strip())
            if spo2_val < 92:
                flags.append("LOW_SPO2")
                if priority > 1:
                    priority = 1
                    reason = f"Critically low SpO2 in uploaded report: {spo2_val}%."
        except (ValueError, AttributeError):
            pass

    # ── Priority 4: known chronic conditions with mild symptoms ──────────────
    if priority == 5:
        chronic_conditions = ["diabetes", "hypertension", "asthma", "heart disease"]
        for cond in chronic_conditions:
            if cond in history:
                priority = 4
                reason = f"Known chronic condition ({cond}) with mild reported symptoms."
                flags.append("CHRONIC_CONDITION")
                break

    return {
        "priority_score": priority,
        "reason": reason,
        "flags": flags
    }
