"""Regression guards for the supervised campaign browser state machine."""
from lapidus_vaults.operations.campaign_ui import LIVE_CAMPAIGN_HTML
from lapidus_vaults.local_console import HTML

def test_post_submission_failure_keeps_hash_and_recovery_path():
    assert "campaignSubmissionPhase='submitted_awaiting_receipt'" in LIVE_CAMPAIGN_HTML
    assert "TRANSACTION SUBMITTED. Receipt check failed:" in LIVE_CAMPAIGN_HTML
    assert "Do not resend. Use Recheck campaign receipt or recovery." in LIVE_CAMPAIGN_HTML
    assert "campaignLastTx.tx_hash" in LIVE_CAMPAIGN_HTML
    assert "campaignLastTx.quote_id" in LIVE_CAMPAIGN_HTML

def test_campaign_ui_uses_distinct_presubmission_failure_message():
    assert "No transaction hash received by LVS." in LIVE_CAMPAIGN_HTML
    assert "Authorization journaled, but wallet submission is unconfirmed." in LIVE_CAMPAIGN_HTML

def test_campaign_ui_disables_repeated_authorization():
    assert "campaignCurrentQuote=null;" in LIVE_CAMPAIGN_HTML
    assert "document.getElementById('campaignAuthorize').disabled=true;" in LIVE_CAMPAIGN_HTML
    assert "campaignVerifyWallet()" in LIVE_CAMPAIGN_HTML
