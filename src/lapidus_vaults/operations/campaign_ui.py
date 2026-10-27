"""Manual MetaMask controls for the next campaign action."""
LIVE_CAMPAIGN_HTML = r"""
<section style="border:1px solid #415469;padding:16px;margin-top:20px;border-radius:8px">
<h2>Next campaign step — supervised execution</h2>
<p>Uses the exact vault and amount from the active campaign. Fresh simulation is mandatory. Each transaction requires a separate MetaMask confirmation.</p>
<button id="campaignQuote">Request next-step quote</button>
<button id="campaignAuthorize" disabled>Review and authorize campaign step</button>
<button id="campaignReceipt" disabled>Recheck campaign receipt</button>
<pre id="campaignTxOutput">No campaign quote requested.</pre>
</section>
<script>
let campaignCurrentQuote=null;
let campaignLastTx=null;
const campaignOut=(value)=>document.getElementById('campaignTxOutput').textContent=value;
async function campaignVerifyWallet(){
 if(!window.ethereum)throw Error('MetaMask unavailable');
 const accounts=await window.ethereum.request({method:'eth_accounts'});
 const network=await window.ethereum.request({method:'eth_chainId'});
 if(accounts[0]?.toLowerCase()!==settings.wallet.toLowerCase() || network!=='0x2105')
  throw Error('Wrong wallet or Base network');
}
async function campaignPost(endpoint,body){
 const res=await fetch(endpoint,{method:'POST',credentials:'same-origin',
   headers:{'Content-Type':'application/json','X-LVS-Token':settings.token},
   body:JSON.stringify(body)});
 const result=await res.json();
 if(!res.ok)throw Error(result.error||'Campaign preflight refused');
 return result;
}
document.getElementById('campaignQuote').onclick=async()=>{
 campaignCurrentQuote=null;
 document.getElementById('campaignAuthorize').disabled=true;
 try{
  await campaignVerifyWallet();
  campaignCurrentQuote=await campaignPost('/api/campaign/quote',{});
  campaignOut(JSON.stringify(campaignCurrentQuote,null,2));
  document.getElementById('campaignAuthorize').disabled=false;
 }catch(e){campaignOut('Campaign quote blocked: '+e.message)}
};
document.getElementById('campaignAuthorize').onclick=async()=>{
 const quoted=campaignCurrentQuote;
 campaignCurrentQuote=null;
 document.getElementById('campaignAuthorize').disabled=true;
 if(!quoted)return;
 try{
  await campaignVerifyWallet();
  const message='Authorize campaign '+quoted.campaign_id+' step '+quoted.sequence+
   ': '+quoted.stage+' '+(quoted.amount_units/1000000)+' USDC? Destination '+
   quoted.to+'. Check MetaMask carefully. No automatic subsequent step.';
  if(!window.confirm(message))return;
  const ready=await campaignPost('/api/campaign/consume',{quote_id:quoted.quote_id});
  await campaignVerifyWallet();
  const tx={from:ready.wallet,to:ready.to,data:ready.data,value:ready.value,gas:ready.gas};
  const hash=await window.ethereum.request({method:'eth_sendTransaction',params:[tx]});
  campaignLastTx={quote_id:quoted.quote_id,tx_hash:hash,stage:ready.stage};
  document.getElementById('campaignReceipt').disabled=false;
  campaignOut('Submitted '+hash+'. Checking receipt...');
  const endpoint=ready.stage==='withdraw'?'/api/withdraw/check':'/api/deposit/check';
  const receipt=await campaignPost(endpoint,{quote_id:quoted.quote_id,tx_hash:hash});
  campaignOut(JSON.stringify(receipt,null,2));
 }catch(e){
  campaignOut('Stopped: '+e.message+'. If MetaMask submitted, check wallet activity before any new attempt.');
 }
};
document.getElementById('campaignReceipt').onclick=async()=>{
 if(!campaignLastTx)return;
 try{
  await campaignVerifyWallet();
  const endpoint=campaignLastTx.stage==='withdraw'?'/api/withdraw/check':'/api/deposit/check';
  campaignOut(JSON.stringify(await campaignPost(endpoint,{
   quote_id:campaignLastTx.quote_id,tx_hash:campaignLastTx.tx_hash}),null,2));
 }catch(e){campaignOut('Campaign receipt check failed: '+e.message)}
};
</script>
"""
