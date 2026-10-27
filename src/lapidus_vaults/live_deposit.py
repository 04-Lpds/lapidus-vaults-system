"""Conservative single-action Base USDC deposit authorization preflight.

No signer and no broadcaster. Each independently confirmed MetaMask transaction
must use a freshly obtained quote. This is NOT a vault risk assessment.
"""
from __future__ import annotations
import secrets
import time
from .allocator import BASE_CHAIN, BASE_USDC, address_ok
from .rpc_verify import ReadOnlyRPC, RPCError, BALANCE_OF, ASSET, _address_word, _word
from .unsigned_candidates import APPROVE, DEPOSIT
from .receipt_events import reconcile_transfer_logs

ALLOWANCE="0xdd62ed3e"
PREVIEW_DEPOSIT="0xef8b30f7"
MAX_UNITS=10_000_000
MIN_GAS_RESERVE_WEI=2_000_000_000_000_000
MAX_QUOTE_AGE_SECONDS=90
MAX_BLOCK_AGE=10

class DepositQuotes:
    def __init__(self):
        self.pending={}
        self.consumed={}

    def quote(self,rpc:ReadOnlyRPC,*,wallet:str,vault:str,approved_vaults:tuple[str,...],
              amount_units:int)->dict:
        if not address_ok(wallet) or vault.lower() not in approved_vaults:
            raise ValueError("Wallet or vault not approved")
        if type(amount_units) is not int or not 0<amount_units<=MAX_UNITS:
            raise ValueError("Deposit must be 1..10,000,000 USDC raw units (<=10 USDC)")
        if rpc.chain_id()!=BASE_CHAIN:
            raise ValueError("Wrong chain")
        block=rpc.block_number()
        if rpc.call_address(vault,ASSET,block)!=BASE_USDC:
            raise ValueError("Underlying asset mismatch")
        wallet_usdc=rpc.call_uint(BASE_USDC,BALANCE_OF,block,_address_word(wallet))
        if wallet_usdc<amount_units:
            raise ValueError("Insufficient wallet USDC")
        eth=rpc.eth_balance(wallet,block)
        if eth<MIN_GAS_RESERVE_WEI:
            raise ValueError("Insufficient ETH gas reserve")
        allowance=rpc.call_uint(BASE_USDC,ALLOWANCE,block,_address_word(wallet),_address_word(vault))
        if allowance>MAX_UNITS:
            raise ValueError("Existing allowance exceeds the strict single-action safety cap")
        if allowance<amount_units:
            if allowance!=0:
                raise ValueError("Nonzero insufficient approval: manually revoke before proceeding")
            stage="approve"
            destination=BASE_USDC
            data=APPROVE+_address_word(vault)+_word(amount_units)
        else:
            stage="deposit"
            destination=vault
            data=DEPOSIT+_word(amount_units)+_address_word(wallet)
            shares=rpc.call_uint(vault,PREVIEW_DEPOSIT,block,_word(amount_units))
            if shares<=0:
                raise ValueError("No shares quoted by previewDeposit")
            response=rpc.simulate_call(wallet,vault,data,block)
            if not isinstance(response,str) or len(response)!=66 or int(response,16)<=0:
                raise ValueError("Deposit did not return positive shares in eth_call")
        estimated=rpc.estimate_gas(wallet,destination,data)
        if not 21000<=estimated<=700000:
            raise ValueError("Gas estimate outside strict policy range")
        gas_limit=min(700000,estimated*13//10+10000)
        quoted_gas_price=rpc.gas_price_wei()
        if not 0<quoted_gas_price<=100_000_000_000:
            raise ValueError("Unsafe gas price quote")
        if eth < MIN_GAS_RESERVE_WEI+gas_limit*quoted_gas_price*3:
            raise ValueError("ETH is insufficient for reserve and conservative gas estimate")
        # No estimate of Base L1-data fee or queued transactions: force manual review.
        quote_id=secrets.token_urlsafe(24)
        self.pending.clear()  # invalidate previous quotes from the console
        created=time.monotonic()
        result={"quote_id":quote_id,"stage":stage,"wallet":wallet.lower(),"vault":vault.lower(),
                "chain_id":BASE_CHAIN,"observed_block":block,"amount_units":amount_units,
                "to":destination,"data":data,"value":"0x0","gas":hex(gas_limit),
                "gas_price_observed_wei":quoted_gas_price,"approval":stage=="approve",
                "expires_in_seconds":MAX_QUOTE_AGE_SECONDS,
                "warning":("Exact-amount USDC approval only." if stage=="approve" else
                           "Deposit simulated individually; vault adapter capacity and exit liquidity not independently verified.")}
        self.pending[quote_id]=(created,result)
        return result

    def consume(self,rpc:ReadOnlyRPC,quote_id:str)->dict:
        stored=self.pending.pop(quote_id,None)
        if stored is None:
            raise ValueError("Unknown or already-consumed quote")
        created,quote=stored
        if time.monotonic()-created>MAX_QUOTE_AGE_SECONDS:
            raise ValueError("Quote expired")
        if rpc.chain_id()!=BASE_CHAIN or rpc.block_number()-quote["observed_block"]>MAX_BLOCK_AGE:
            raise ValueError("Quote stale or wrong network")
        # Requote against current state. The new quote invalidates the old one.
        # Require exact same stage, recipient, calldata and gas cap.
        verified=self.quote(rpc,wallet=quote["wallet"],vault=quote["vault"],
                            approved_vaults=(quote["vault"],),amount_units=quote["amount_units"])
        consistency=("stage","to","data","wallet","value")
        if quote["stage"]=="withdraw":
            consistency+=("expected_burned_shares",)
        if any(verified[k]!=quote[k] for k in consistency):
            self.pending.clear()
            if quote["stage"]=="withdraw" and verified["expected_burned_shares"]!=quote["expected_burned_shares"]:
                raise ValueError("Withdrawal share preview changed; request and review a fresh quote")
            raise ValueError("Transaction details changed; request and review a fresh quote")
        if int(verified["gas"],16)>int(quote["gas"],16):
            self.pending.clear()
            raise ValueError("Gas estimate increased beyond quoted limit; request a fresh quote")
        self.pending.clear()
        self.consumed[quote_id]=quote
        fields=("stage","to","data","value","gas","wallet","vault",
                "chain_id","amount_units","observed_block")
        if quote["stage"]=="withdraw":
            fields+=("reported_max_withdraw_units","max_withdraw_is_executable_limit","expected_burned_shares")
        return {k:quote[k] for k in fields}

    def check_receipt(self,rpc:ReadOnlyRPC,quote_id:str,tx_hash:str)->dict:
        """Compare the exact sent calldata, even if receipt is pending."""
        q=self.consumed.get(quote_id)
        if q is None or not isinstance(tx_hash,str) or len(tx_hash)!=66:
            raise ValueError("Unknown consumed quote or invalid transaction hash")
        bytes.fromhex(tx_hash[2:])
        if rpc.chain_id()!=BASE_CHAIN:
            raise ValueError("Wrong chain")
        tx=rpc.request("eth_getTransactionByHash",[tx_hash])
        receipt=rpc.request("eth_getTransactionReceipt",[tx_hash])
        if tx is None:
            return {"status":"PENDING_NOT_FOUND","tx_hash":tx_hash,"matched":False}
        if (str(tx.get("hash","")).lower()!=tx_hash.lower()
            or str(tx.get("from","")).lower()!=q["wallet"]
            or str(tx.get("to","")).lower()!=q["to"]
            or str(tx.get("input","")).lower()!=q["data"].lower()
            or int(tx.get("value","0x0"),16)!=0
            or int(tx.get("chainId","0x0"),16)!=BASE_CHAIN):
            raise ValueError("Observed transaction differs from LVS quote")
        if receipt is None:
            return {"status":"PENDING_MATCHED_INPUT","tx_hash":tx_hash,"matched":True}
        if str(receipt.get("transactionHash","")).lower()!=tx_hash.lower():
            raise ValueError("Receipt hash mismatch")
        if int(receipt.get("status","0x0"),16)!=1:
            return {"status":"REVERTED","tx_hash":tx_hash,"matched":True}
        block=int(receipt["blockNumber"],16)
        if block < q["observed_block"]:
            raise ValueError("Transaction predates quoted observation")
        # This is a block-to-block observation, not a transaction-scoped proof.
        if block<1:
            raise ValueError("Cannot compare genesis balances")
        def balances(height):
            return (rpc.call_uint(BASE_USDC,BALANCE_OF,height,_address_word(q["wallet"])),
                    rpc.call_uint(q["vault"],BALANCE_OF,height,_address_word(q["wallet"])))
        cash_before,shares_before=balances(block-1)
        cash_after,shares_after=balances(block)
        if q["stage"]=="approve":
            after_allowance=rpc.call_uint(BASE_USDC,ALLOWANCE,block,
                                         _address_word(q["wallet"]),_address_word(q["vault"]))
            delta_ok=cash_before==cash_after and shares_before==shares_after and after_allowance>=q["amount_units"]
        else:
            delta_ok=(cash_before-cash_after==q["amount_units"]
                      and shares_after>shares_before)
        if q["stage"]=="withdraw":
            delta_ok=(cash_after-cash_before==q["amount_units"] and shares_after<shares_before)
        event_result=reconcile_transfer_logs(receipt,q["wallet"],q["vault"],q["amount_units"],q["stage"])
        return {"status":"RECEIPT_AND_BLOCK_MATCH" if delta_ok and event_result["matched"] else "RECONCILIATION_MISMATCH",
                "transaction_event_reconciliation":event_result,
                "tx_hash":tx_hash,"matched":True,"block":block,
                "gas_used":int(receipt["gasUsed"],16),"stage":q["stage"],
                "cash_delta_units":cash_after-cash_before,"share_delta":shares_after-shares_before,
                "balance_match":delta_ok,
                "financial_reconciliation_complete":False,
                "warning":"Block-level deltas may contain unrelated same-block activity; not a transaction-causal proof."}
