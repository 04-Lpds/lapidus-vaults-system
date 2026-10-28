"""Operator entry-point and receipt-polling safety regressions."""
import pytest
from lapidus_vaults import operator
from lapidus_vaults.operations.campaign_ui import LIVE_CAMPAIGN_HTML


def test_operator_dispatch_monitor(monkeypatch):
    from lapidus_vaults import monitor_once
    seen = {}
    def run(args):
        seen["args"] = args
        return 0
    monkeypatch.setattr(monitor_once, "main", run)
    assert operator.main(["monitor", "--wallet", "0x123", "--help"]) == 0
    assert seen["args"] == ["--wallet", "0x123", "--help"]


def test_operator_rejects_unknown_command():
    with pytest.raises(SystemExit):
        operator.main(["something-else"])


def test_receipt_polling_never_resubmits():
    assert "async function campaignPollReceipt" in LIVE_CAMPAIGN_HTML
    assert "await campaignPollReceipt(campaignLastTx)" in LIVE_CAMPAIGN_HTML
    assert "if(i<attempts-1)await new Promise" in LIVE_CAMPAIGN_HTML
    poller = LIVE_CAMPAIGN_HTML.split("async function campaignPollReceipt", 1)[1].split(
        "function campaignReceiptDisplay", 1
    )[0]
    assert "eth_sendTransaction" not in poller
    assert "Do not resend" in poller
