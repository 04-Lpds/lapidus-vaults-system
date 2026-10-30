"""Failure-injection regression: no second reservation after uncertain send."""
import pytest
from lapidus_vaults.operations.auto_journal import reserve, mark_submitted, current

W="0x"+"1"*40
TO="0x"+"2"*40
DATA="0x12345678"
TX="0x"+"a"*64

def args(path,seq,quote):
    return dict(db_path=str(path),campaign_id="restart-trial",sequence=seq,
        quote_id=quote,wallet=W,to_address=TO,calldata=DATA,amount_units=500000)

def test_crash_window_unknown_outcome_cannot_be_replayed(tmp_path):
    db=tmp_path/"restart.sqlite3"
    first=reserve(**args(db,1,"quote-1"))
    assert first["state"]=="SUBMISSION_UNKNOWN"
    with pytest.raises(ValueError,match="Duplicate autonomous reservation"):
        reserve(**args(db,1,"quote-1"))
    with pytest.raises(ValueError,match="Prior autonomous transaction unresolved"):
        reserve(**args(db,2,"quote-2"))
    assert current(str(db),"restart-trial")[0]["tx_hash"] is None

def test_crash_after_hash_journaled_never_replaces_hash(tmp_path):
    db=tmp_path/"restart.sqlite3"
    reserve(**args(db,1,"quote-1"))
    mark_submitted(str(db),campaign_id="restart-trial",sequence=1,
        quote_id="quote-1",tx_hash=TX)
    with pytest.raises(ValueError,match="Cannot replace bound transaction hash"):
        mark_submitted(str(db),campaign_id="restart-trial",sequence=1,
            quote_id="quote-1",tx_hash="0x"+"b"*64)
    with pytest.raises(ValueError,match="Prior autonomous transaction unresolved"):
        reserve(**args(db,2,"quote-2"))
    assert current(str(db),"restart-trial")[0]["tx_hash"]==TX
