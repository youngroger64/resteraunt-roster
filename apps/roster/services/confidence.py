def score_assignment(*, copied: bool, department_match: bool) -> int:
    score = 50
    if copied:
        score += 30
    if department_match:
        score += 20
    return min(score, 100)
