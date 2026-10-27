"""Local console is loopback-only, CSRF protected, and has no send flow."""
from http.client import HTTPConnection
import threading
from unittest.mock import patch

from lapidus_vaults.local_console import make_handler, HTML
from http.server import ThreadingHTTPServer
from lapidus_vaults.fork_roundtrip import ACTOR


def test_console_has_no_broadcast_wallet_methods():
    assert "eth_requestAccounts" in HTML
    assert "eth_chainId" in HTML
    assert "eth_sendTransaction" not in HTML
    assert "eth_signTransaction" not in HTML


def test_console_rejects_bad_token_and_host(tmp_path):
    token="test-random-token"
    handler=make_handler(ACTOR,"dummy",str(tmp_path/"test.db"),token)
    with ThreadingHTTPServer(("127.0.0.1",0),handler) as server:
        t=threading.Thread(target=server.serve_forever,daemon=True)
        t.start()
        try:
            conn=HTTPConnection("127.0.0.1",server.server_port)
            conn.request("POST","/api/prepare",body="{}",headers={
                "Host":"127.0.0.1:8765","Content-Length":"2","X-LVS-Token":"wrong"})
            r=conn.getresponse()
            assert r.status==403
            r.read()
            conn.close()
            conn=HTTPConnection("127.0.0.1",server.server_port)
            conn.request("GET","/",headers={"Host":"evil.example"})
            r=conn.getresponse()
            assert r.status==404
            r.read()
            conn.close()
        finally:
            server.shutdown()
            t.join()
