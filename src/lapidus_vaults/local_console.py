"""Local, loopback-only LVS proposal console with manual MetaMask deposit.

Serves a single lightweight browser UI. Wallet connection only requests
eth_accounts/eth_requestAccounts and eth_chainId; no eth_sendTransaction.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import sys
from urllib.parse import urlsplit

from .allocator import address_ok
from .client import MorphoClient
from .live_readonly_plan import build_live_readonly_plan
from .rpc_verify import ReadOnlyRPC
from .transaction_review import prepare_manifest, store_manifest
from .wallet_plan import read_policy
from .live_deposit import DepositQuotes
from .live_withdraw import WithdrawalQuotes
from .live_deposit_ui import LIVE_DEPOSIT_HTML
from .live_withdraw_ui import LIVE_WITHDRAW_HTML
import sqlite3
from datetime import datetime, timezone

HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LVS · Local Review Console</title>
<style>
body{background:#101820;color:#edf3fa;font:16px system-ui;margin:0 auto;max-width:850px;padding:35px}
h1{font-size:27px}p{line-height:1.5}button{background:#dceeff;color:#13202f;border:0;
border-radius:7px;padding:12px 17px;font-weight:650;cursor:pointer;margin:8px 8px 8px 0}
pre{background:#1b2836;white-space:pre-wrap;overflow-wrap:anywhere;padding:20px;border-radius:8px}
small{color:#a9bdcf}.warning{color:#ffe1ac}.status{border:1px solid #415469;padding:16px;border-radius:8px}
</style></head><body>
<h1>Lapidus Vault Systems</h1><p>Local proposal console — <strong>READ ONLY</strong>.
This interface cannot sign or broadcast transactions. Do not submit the displayed calldata.</p>
<div class="status"><div>Expected wallet: <code id="expected"></code></div>
<div>Wallet status: <strong id="wallet">Not connected</strong></div>
<div>Base network: <strong id="network">Not checked</strong></div>
<button id="connect">Connect MetaMask (identity only)</button>
<button id="prepare">Prepare &amp; journal proposal</button>
</div><p class="warning" id="notice">No unattended on-chain execution is available.</p>
__LIVE_UI__\n<h2>Latest proposal</h2><pre id="report">No proposal prepared.</pre>
<small>All calculations are observations and unsigned intentions, not investment or execution approval.</small>
<script>
const settings=__CONFIG__;
document.getElementById('expected').textContent=settings.wallet;
const show=(s)=>document.getElementById('notice').textContent=s;
document.getElementById('connect').onclick=async()=>{
 try{
  if(!window.ethereum)throw Error('MetaMask provider not found');
  const accounts=await window.ethereum.request({method:'eth_requestAccounts'});
  const chain=await window.ethereum.request({method:'eth_chainId'});
  const ok=accounts[0]?.toLowerCase()===settings.wallet.toLowerCase();
  document.getElementById('wallet').textContent=ok?'Expected wallet confirmed':'Wallet mismatch — review blocked';
  document.getElementById('network').textContent=chain==='0x2105'?'Base (8453)':'Wrong network';
  show(ok&&chain==='0x2105'?'Identity and network match. No signature requested.':'Verify selected wallet and Base network.');
 }catch(e){show('Wallet verification failed: '+e.message)}
};
document.getElementById('prepare').onclick=async()=>{
 show('Reading live Base state and creating review-only proposal...');
 try{
  const r=await fetch('/api/prepare',{method:'POST',headers:{'Content-Type':'application/json',
    'X-LVS-Token':settings.token},body:'{}',credentials:'same-origin'});
  const data=await r.json();
  document.getElementById('report').textContent=JSON.stringify(data,null,2);
  show(r.ok?'Journal saved. REVIEW ONLY — no transaction submitted.':'Preparation failed. No transaction submitted.');
 }catch(e){show('Local request failed: '+e.message)}
};
</script></body></html>"""


def make_handler(wallet: str, allowlist: str, db_path: str, token: str):
    quotes = DepositQuotes()
    withdrawals = WithdrawalQuotes()
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="application/json"):
            payload=body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",content_type+"; charset=utf-8")
            self.send_header("Content-Length",str(len(payload)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def _host_ok(self):
            host=self.headers.get("Host","")
            return host in {"127.0.0.1:8765","localhost:8765"}

        def do_GET(self):
            if not self._host_ok() or urlsplit(self.path).path!="/":
                return self._send(404,'{"error":"not found"}')
            config=json.dumps({"wallet":wallet,"token":token}).replace("<","\\u003c")
            self._send(200,HTML.replace("__CONFIG__",config).replace("__LIVE_UI__",LIVE_DEPOSIT_HTML + LIVE_WITHDRAW_HTML),"text/html")

        def do_POST(self):
            if not self._host_ok() or self.path not in {"/api/prepare","/api/deposit/quote","/api/deposit/consume","/api/deposit/check","/api/withdraw/quote","/api/withdraw/consume","/api/withdraw/check"}:
                return self._send(404,'{"error":"not found"}')
            if self.headers.get("X-LVS-Token")!=token:
                return self._send(403,'{"error":"invalid local token"}')
            origin=self.headers.get("Origin")
            if origin not in {None,"http://127.0.0.1:8765","http://localhost:8765"}:
                return self._send(403,'{"error":"invalid origin"}')
            try:
                length=int(self.headers.get("Content-Length","-1"))
                if not 0<=length<=2048:
                    return self._send(400,'{"error":"invalid length"}')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError("Expected object")
            except (ValueError,UnicodeDecodeError):
                return self._send(400,'{"error":"invalid body"}')
            if self.path!="/api/prepare":
                try:
                    vaults=read_policy(allowlist).vault_addresses
                    with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc:
                        if self.path in {"/api/deposit/quote","/api/withdraw/quote"}:
                            if set(data)!={"amount_units"}:
                                raise ValueError("Unexpected fields")
                            engine=withdrawals if self.path=="/api/withdraw/quote" else quotes
                            result=engine.quote(rpc,wallet=wallet,vault=vaults[0],
                                                approved_vaults=vaults,amount_units=data["amount_units"])
                        elif self.path in {"/api/deposit/consume","/api/withdraw/consume"}:
                            if set(data)!={"quote_id"} or not isinstance(data["quote_id"],str):
                                raise ValueError("Unexpected fields")
                            engine=withdrawals if self.path=="/api/withdraw/consume" else quotes
                            result=engine.consume(rpc,data["quote_id"])
                            # Save intended exact transaction before exposing it to MetaMask.
                            with sqlite3.connect(db_path,timeout=5) as db:
                                db.execute("""CREATE TABLE IF NOT EXISTS live_deposit_authorizations (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    prepared_at TEXT NOT NULL,
                                    quote_id TEXT UNIQUE NOT NULL,
                                    wallet TEXT NOT NULL,
                                    vault TEXT NOT NULL,
                                    stage TEXT NOT NULL,
                                    amount_units INTEGER NOT NULL,
                                    transaction_json TEXT NOT NULL)""")
                                db.execute("""INSERT INTO live_deposit_authorizations
                                    (prepared_at,quote_id,wallet,vault,stage,amount_units,transaction_json)
                                    VALUES (?,?,?,?,?,?,?)""",
                                    (datetime.now(timezone.utc).isoformat(),data["quote_id"],
                                     result["wallet"],result["vault"],result["stage"],
                                     result["amount_units"],json.dumps(result,sort_keys=True)))
                        else:
                            if set(data)!={"quote_id","tx_hash"} or not all(isinstance(v,str) for v in data.values()):
                                raise ValueError("Unexpected fields")
                            engine=withdrawals if self.path=="/api/withdraw/check" else quotes
                            # Recover a journaled authorization after a server restart.
                            # Only receipt checking is restored; never a spent quote's send permission.
                            if data["quote_id"] not in engine.consumed:
                                with sqlite3.connect(db_path,timeout=5) as db:
                                    row=db.execute("""SELECT wallet,vault,stage,amount_units,transaction_json
                                        FROM live_deposit_authorizations WHERE quote_id=?""",
                                        (data["quote_id"],)).fetchone()
                                if row is None:
                                    raise ValueError("Unknown consumed quote or invalid transaction hash")
                                saved_wallet,saved_vault,saved_stage,saved_amount,raw=row
                                expected_stage="withdraw" if self.path=="/api/withdraw/check" else None
                                if (saved_wallet!=wallet.lower() or saved_vault not in vaults
                                    or (expected_stage and saved_stage!=expected_stage)
                                    or (not expected_stage and saved_stage not in {"approve","deposit"})):
                                    raise ValueError("Unknown consumed quote or invalid transaction hash")
                                tx=json.loads(raw)
                                if (tx.get("wallet")!=saved_wallet or tx.get("vault")!=saved_vault
                                    or tx.get("stage")!=saved_stage or tx.get("amount_units")!=saved_amount
                                    or tx.get("chain_id")!=8453):
                                    raise ValueError("Invalid journaled authorization")
                                engine.consumed[data["quote_id"]]=tx
                            result=engine.check_receipt(rpc,data["quote_id"],data["tx_hash"])
                            result["quote_id"]=data["quote_id"]
                            with sqlite3.connect(db_path,timeout=5) as db:
                                db.execute("""CREATE TABLE IF NOT EXISTS live_deposit_receipt_checks (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    checked_at TEXT NOT NULL,
                                    tx_hash TEXT NOT NULL,
                                    status TEXT NOT NULL,
                                    result_json TEXT NOT NULL)""")
                                db.execute("""INSERT INTO live_deposit_receipt_checks
                                    (checked_at,tx_hash,status,result_json) VALUES (?,?,?,?)""",
                                    (datetime.now(timezone.utc).isoformat(),data["tx_hash"],result["status"],
                                    json.dumps(result,sort_keys=True)))
                                db.execute("""CREATE TABLE IF NOT EXISTS transaction_lifecycle (
                                    tx_hash TEXT PRIMARY KEY,
                                    quote_id TEXT NOT NULL,
                                    wallet TEXT NOT NULL,
                                    vault TEXT NOT NULL,
                                    stage TEXT NOT NULL,
                                    status TEXT NOT NULL,
                                    checked_at TEXT NOT NULL,
                                    actual_fee_wei TEXT,
                                    result_json TEXT NOT NULL)""")
                                journaled=engine.consumed[data["quote_id"]]
                                db.execute("""INSERT INTO transaction_lifecycle
                                    (tx_hash,quote_id,wallet,vault,stage,status,checked_at,actual_fee_wei,result_json)
                                    VALUES (?,?,?,?,?,?,?,?,?)
                                    ON CONFLICT(tx_hash) DO UPDATE SET
                                      status=excluded.status,
                                      checked_at=excluded.checked_at,
                                      actual_fee_wei=excluded.actual_fee_wei,
                                      result_json=excluded.result_json
                                    WHERE transaction_lifecycle.quote_id=excluded.quote_id""",
                                    (data["tx_hash"].lower(),data["quote_id"],journaled["wallet"],
                                     journaled["vault"],journaled["stage"],result["status"],
                                     datetime.now(timezone.utc).isoformat(),
                                     str(result["total_fee_wei"]) if result.get("total_fee_wei") is not None else None,
                                     json.dumps(result,sort_keys=True)))
                    return self._send(200,json.dumps(result))
                except ValueError as exc:
                    allowed = ("Quote expired", "Quote stale or wrong network",
                               "Unknown or already-consumed quote", "State changed since quote", "Withdrawal share preview changed",
                               "Transaction details changed", "Gas estimate increased",
                               "Insufficient wallet USDC", "Insufficient ETH gas reserve",
                               "ETH is insufficient", "Existing allowance exceeds",
                               "Nonzero insufficient approval",
                               "Requested assets exceed maxWithdraw",
                               "Insufficient shares or invalid previewWithdraw",
                               "Simulated burned shares differ from previewWithdraw",
                               "Malformed withdrawal eth_call result",
                               "Withdrawal eth_call did not return positive burned shares")
                    detail = str(exc) if any(str(exc).startswith(x) for x in allowed) else "Preflight rejected request"
                    return self._send(409,json.dumps({"error":detail,
                        "submitted_by_backend":False}))
                except Exception:
                    return self._send(503,json.dumps({"error":"Upstream RPC or local journal failure",
                        "submitted_by_backend":False}))
            if data!={}:
                return self._send(400,'{"error":"expected empty object"}')
            try:
                policy=read_policy(allowlist)
                with ReadOnlyRPC(os.environ["BASE_RPC_URL"]) as rpc, MorphoClient() as morpho:
                    report=build_live_readonly_plan(wallet,allowlist,rpc,morpho)
                manifest=prepare_manifest(report,policy.vault_addresses,cap_usdc=100)
                row_id=store_manifest(db_path,manifest)
                self._send(200,json.dumps({"manifest_id":row_id,**manifest}))
            except Exception:
                # Never echo RPC URLs, upstream responses, or credentials in browser.
                self._send(503,'{"error":"verification or journal failed; no transaction attempted"}')

        def log_message(self,fmt,*args):
            pass

    return Handler


def main(argv=None):
    p=argparse.ArgumentParser(description="Local LVS read-only browser proposal console")
    p.add_argument("--wallet",required=True)
    p.add_argument("--allowlist",required=True)
    p.add_argument("--db",default="data/lapidus.sqlite3")
    args=p.parse_args(argv)
    if not address_ok(args.wallet) or not os.environ.get("BASE_RPC_URL"):
        print("Public wallet and BASE_RPC_URL required",file=sys.stderr)
        return 1
    token=secrets.token_urlsafe(32)
    try:
        server=ThreadingHTTPServer(("127.0.0.1",8765),
             make_handler(args.wallet.lower(),args.allowlist,args.db,token))
        print("LVS read-only console: http://127.0.0.1:8765/")
        print("No signing or broadcasting. Ctrl+C to stop.")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except OSError:
        print("Cannot bind local port 8765",file=sys.stderr)
        return 1
    return 0

if __name__=="__main__":
    raise SystemExit(main())
