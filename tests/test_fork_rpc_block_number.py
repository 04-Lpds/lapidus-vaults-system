"""Local Anvil RPC permissions regression: block-number read must remain allowed."""
from unittest.mock import Mock
from lapidus_vaults.fork_roundtrip import ForkRPC


def test_block_number_read_allowed_without_network():
    rpc = ForkRPC("http://127.0.0.1:8545")
    try:
        response = Mock()
        response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x123"}
        rpc.client.post = Mock(return_value=response)
        assert rpc.call("eth_blockNumber", []) == "0x123"
        rpc.client.post.assert_called_once()
    finally:
        rpc.client.close()
