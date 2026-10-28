"""Crash recovery tests: never send the same autonomous intent twice."""
import pytest
from lapidus_vaults.operations.auto_journal import reserve, mark_submitted, mark_verified, current

W = "0x" + "1"*40
V = "0x" + "2"*40
H1 = "0x" + "a"*64
H2 = "0x" + "b"*64

def claim(db, seq=1, quote="quote-1"):
    return reserve(db, campaign_id="c", sequence=seq, quote_id=quote,
                   wallet=W, to_address=V, calldata="0x12345678", amount_units=1_000_000)

def checked(tx_hash):
    return {"status": "RECEIPT_AND_BLOCK_MATCH", "tx_hash": tx_hash,
            "matched": True, "balance_match": True,
            "transaction_event_reconciliation": {"matched": True}}

def test_crash_after_reservation_blocks_replay(tmp_path):
    db = str(tmp_path / "auto.db")
    claim(db)
    assert current(db, "c")[0]["state"] == "SUBMISSION_UNKNOWN"
    with pytest.raises(ValueError, match="Duplicate"):
        claim(db)
    with pytest.raises(ValueError, match="unresolved"):
        claim(db, 2, "quote-2")

def test_receipt_recovery_and_following_action(tmp_path):
    db = str(tmp_path / "auto.db")
    claim(db)
    mark_submitted(db, campaign_id="c", sequence=1, quote_id="quote-1", tx_hash=H1)
    assert current(db,"c")[0]["tx_hash"] == H1
    with pytest.raises(ValueError, match="replace"):
        mark_submitted(db, campaign_id="c", sequence=1, quote_id="quote-1", tx_hash=H2)
    with pytest.raises(ValueError, match="Insufficient"):
        mark_verified(db, campaign_id="c", sequence=1, tx_hash=H1,
                      verified={"status":"RECEIPT_AND_BLOCK_MATCH"})
    mark_verified(db, campaign_id="c", sequence=1, tx_hash=H1, verified=checked(H1))
    claim(db, 2, "quote-2")
    assert [r["state"] for r in current(db,"c")] == ["CONFIRMED","SUBMISSION_UNKNOWN"]

def test_cannot_claim_out_of_order_or_above_limit(tmp_path):
    db = str(tmp_path / "auto.db")
    with pytest.raises(ValueError, match="bounded"):
        reserve(db,campaign_id="c",sequence=1,quote_id="q",wallet=W,
                to_address=V,calldata="0x",amount_units=1_000_001)
    claim(db)
    mark_submitted(db,campaign_id="c",sequence=1,quote_id="quote-1",tx_hash=H1)
    mark_verified(db,campaign_id="c",sequence=1,tx_hash=H1,verified=checked(H1))
    with pytest.raises(ValueError,match="sequence"):
        claim(db,3,"quote-3")
