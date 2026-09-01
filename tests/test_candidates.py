from gold_research.candidates import CANDIDATES


def test_candidates_have_required_fields():
    assert len(CANDIDATES) == 5
    codes = {c.code for c in CANDIDATES}
    assert codes == {"00708L", "00674R", "gold_passbook", "GLD", "IAU"}
    for candidate in CANDIDATES:
        assert candidate.name
        assert candidate.note
        assert candidate.source.name
        assert candidate.source.as_of
