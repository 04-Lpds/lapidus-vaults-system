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

def test_withdraw_receipt_check_recovers_journal_after_restart(tmp_path):
    import json
    import sqlite3
    from unittest.mock import Mock
    from lapidus_vaults.fork_roundtrip import VAULTS
    from lapidus_vaults.local_console import make_handler
    wallet=ACTOR.lower()
    vault=VAULTS[0].lower()
    quote_id="journaled-test-quote"
    tx_hash="0x"+"ab"*32
    db_path=str(tmp_path/"journal.sqlite3")
    saved={"wallet":wallet,"vault":vault,"stage":"withdraw","amount_units":1_000_000,
           "chain_id":8453,"to":vault,"data":"0xb460af94"+"00"*96,
           "value":"0x0","observed_block":100}
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE live_deposit_authorizations
          (quote_id TEXT, wallet TEXT, vault TEXT, stage TEXT,
           amount_units INTEGER, transaction_json TEXT)""")
        db.execute("INSERT INTO live_deposit_authorizations VALUES (?,?,?,?,?,?)",
                   (quote_id,wallet,vault,"withdraw",1_000_000,json.dumps(saved)))
    rpc=Mock()
    rpc.__enter__=Mock(return_value=rpc)
    rpc.__exit__=Mock(return_value=False)
    rpc.chain_id.return_value=8453
    rpc.request.side_effect=lambda method,params: (
      {"hash":tx_hash,"from":wallet,"to":vault,"input":saved["data"],
       "value":"0x0","chainId":"0x2105"} if method=="eth_getTransactionByHash" else None)
    policy=Mock(vault_addresses=(vault,))
    handler=make_handler(wallet,"unused",db_path,"test-token")
    import os
    with patch("lapidus_vaults.local_console.read_policy",return_value=policy), \
         patch("lapidus_vaults.local_console.ReadOnlyRPC",return_value=rpc), \
         patch.dict(os.environ,{"BASE_RPC_URL":"https://unused.example"}):
        with ThreadingHTTPServer(("127.0.0.1",0),handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                conn=HTTPConnection("127.0.0.1",server.server_port)
                payload=json.dumps({"quote_id":quote_id,"tx_hash":tx_hash})
                conn.request("POST","/api/withdraw/check",body=payload,headers={
                  "Host":"127.0.0.1:8765","Content-Type":"application/json",
                  "X-LVS-Token":"test-token"})
                response=conn.getresponse()
                body=json.loads(response.read())
                assert response.status==200
                assert body["status"]=="PENDING_MATCHED_INPUT"
                conn.close()
            finally:
                server.shutdown()
                thread.join()

def test_campaign_status_without_active_campaign_is_read_only(tmp_path):
    import json
    handler=make_handler(ACTOR,"dummy",str(tmp_path/"empty.sqlite"),"test-token")
    with ThreadingHTTPServer(("127.0.0.1",0),handler) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            conn=HTTPConnection("127.0.0.1",server.server_port)
            conn.request("POST","/api/campaign/status",body="{}",headers={
                "Host":"127.0.0.1:8765","Content-Type":"application/json",
                "X-LVS-Token":"test-token"})
            response=conn.getresponse()
            data=json.loads(response.read())
            assert response.status==200
            assert data["status"]=="NO_ACTIVE_CAMPAIGN"
            conn.close()
        finally:
            server.shutdown()
            worker.join()
