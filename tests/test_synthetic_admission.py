"""Synthetic gate tests, without chain access or signing."""
from lapidus_vaults.operations.synthetic_admission import scenario

def test_profitable_admission_is_still_nonexecuting():
    r=scenario("profitable")
    assert r["admission"]["status"]=="ADMISSION_REVIEW_ONLY"
    assert r["campaign_created"] is False and r["submitted"] is False
    assert r["admission"]["execution_enabled"] is False

def test_uneconomic_case_blocks():
    assert scenario("uneconomic")["admission"]["status"]=="ADMISSION_BLOCKED"

def test_tampered_calldata_blocks():
    assert scenario("tampered")["admission"]["status"]=="ADMISSION_BLOCKED"
