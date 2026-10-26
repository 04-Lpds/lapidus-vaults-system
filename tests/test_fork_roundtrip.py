"""Fork harness guardrails; no network access or transactions."""
import pytest
from lapidus_vaults.fork_roundtrip import (
    validate_local_endpoint, ForkRPC, run_fork_test, VAULTS, word, addr_word
)


@pytest.mark.parametrize("url", [
    "https://mainnet.base.org", "http://example.com:8545",
    "http://127.0.0.1", "http://127.0.0.1:8545/path",
    "http://user:pass@localhost:8545",
])
def test_reject_nonlocal_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        validate_local_endpoint(url)


def test_accept_explicit_loopback():
    for url in ("http://127.0.0.1:8545", "http://localhost:8545"):
        validate_local_endpoint(url)


def test_encoding():
    assert len(word(10)) == 64
    assert len(addr_word(VAULTS[0])) == 64
    with pytest.raises(ValueError):
        word(-1)


def test_no_transaction_before_chain_identity_check():
    class WrongChain:
        def __init__(self): self.calls = []
        def call(self, method, args):
            self.calls.append(method)
            return "0x1"
    node = WrongChain()
    with pytest.raises(RuntimeError, match="Base chain"):
        run_fork_test(node, VAULTS[0], 10_000_000, usdc_holder="0x" + "a" * 40)
    assert node.calls == ["eth_chainId"]


def test_reject_unapproved_vault_without_rpc_use():
    class NoRPC:
        def call(self, *args):
            raise AssertionError("Should never call RPC")
    with pytest.raises(ValueError):
        run_fork_test(NoRPC(), "0x" + "b"*40, 10_000_000, usdc_holder="0x" + "a"*40)
