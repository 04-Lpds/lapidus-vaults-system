"""Bounded manual withdrawal quotes, reusing the verified MetaMask quote lifecycle."""
from __future__ import annotations
from .live_deposit import DepositQuotes, MIN_GAS_RESERVE_WEI, MAX_UNITS, MAX_QUOTE_AGE_SECONDS
from .allocator import BASE_CHAIN, BASE_USDC, address_ok
from .rpc_verify import BALANCE_OF, ASSET, _address_word, _word
from .unsigned_candidates import WITHDRAW

PREVIEW_WITHDRAW="0x0a28a477"
MAX_WITHDRAW="0xce96cb77"  # ERC-4626 maxWithdraw(address)
SHARE_DRIFT_BPS=1  # at most 0.01% more shares than originally reviewed

class WithdrawalQuotes(DepositQuotes):
    def quote(self,rpc,*,wallet,vault,approved_vaults,amount_units):
        if not address_ok(wallet) or vault.lower() not in approved_vaults:
            raise ValueError("Unapproved wallet or vault")
        if type(amount_units) is not int or not 0<amount_units<=MAX_UNITS:
            raise ValueError("Withdrawal must be <=10 USDC")
        if rpc.chain_id()!=BASE_CHAIN:
            raise ValueError("Wrong chain")
        block=rpc.block_number()
        if rpc.call_address(vault,ASSET,block)!=BASE_USDC:
            raise ValueError("Underlying mismatch")
        eth=rpc.eth_balance(wallet,block)
        if eth<self.min_gas_reserve_wei:
            raise ValueError("Insufficient ETH reserve")
        shares=rpc.call_uint(vault,BALANCE_OF,block,_address_word(wallet))
        withdrawable=rpc.call_uint(vault,MAX_WITHDRAW,block,_address_word(wallet))
        # Morpho Vault V2 maxWithdraw intentionally always returns zero.
        # Treat it as an observed diagnostic, not an executable liquidity limit.
        # Require positive owned shares, previewWithdraw, and pinned eth_call instead.
        required=rpc.call_uint(vault,PREVIEW_WITHDRAW,block,_word(amount_units))
        if not 0<required<=shares:
            raise ValueError("Insufficient shares or invalid previewWithdraw")
        data=WITHDRAW+_word(amount_units)+_address_word(wallet)+_address_word(wallet)
        out=rpc.simulate_call(wallet,vault,data,block)
        if not isinstance(out,str) or len(out)!=66 or not out.startswith("0x"):
            raise ValueError("Malformed withdrawal eth_call result")
        try:
            simulated_shares=int(out,16)
        except ValueError as exc:
            raise ValueError("Malformed withdrawal eth_call result") from exc
        if simulated_shares!=required:
            raise ValueError("Simulated burned shares differ from previewWithdraw")
        gas_estimate=rpc.estimate_gas(wallet,vault,data)
        if not 21000<=gas_estimate<=700000:
            raise ValueError("Gas estimate outside policy")
        limit=min(700000,gas_estimate*13//10+10000)
        gas_price=rpc.gas_price_wei()
        if not 0<gas_price<=100_000_000_000 or eth<self.min_gas_reserve_wei+3*limit*gas_price:
            raise ValueError("Insufficient ETH for conservative gas budget")
        import secrets,time
        quote_id=secrets.token_urlsafe(24)
        self.pending.clear()
        obj={"quote_id":quote_id,"stage":"withdraw","wallet":wallet.lower(),"vault":vault.lower(),
             "chain_id":BASE_CHAIN,"observed_block":block,"amount_units":amount_units,
             "reported_max_withdraw_units":withdrawable,"max_withdraw_is_executable_limit":False,
             "expected_burned_shares":required,
             "max_burned_shares":required+(required*SHARE_DRIFT_BPS+9999)//10000,
             "to":vault.lower(),"data":data,"value":"0x0","gas":hex(limit),
             "gas_estimate_units":gas_estimate,"gas_price_observed_wei":gas_price,"expires_in_seconds":MAX_QUOTE_AGE_SECONDS,
             "warning":"Morpho V2 maxWithdraw reports zero by design; successful eth_call does not guarantee future liquidity or exit availability."}
        self.pending[quote_id]=(time.monotonic(),obj)
        return obj
