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

ALLOWANCE="0xdd62ed3e"
PREVIEW_DEPOSIT="0xef8b30f7"
MAX_UNITS=10_000_000
MIN_GAS_RESERVE_WEI=2_000_000_000_000_000
MAX_QUOTE_AGE_SECONDS=20
MAX_BLOCK_AGE=10

class DepositQuotes:
    def __init__(self):
        self.pending={}

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
                "warning":"Wallet approval only; live vault capacity/liquidity and investment safety not proven."}
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
        if any(verified[k]!=quote[k] for k in ("stage","to","data","wallet","value")):
            self.pending.clear()
            raise ValueError("State changed since quote; re-review required")
        self.pending.clear()
        return {k:quote[k] for k in ("stage","to","data","value","gas","wallet","vault",
                                     "chain_id","amount_units","observed_block")}
