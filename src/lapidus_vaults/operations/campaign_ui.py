"""Manual MetaMask controls for the next campaign action."""
LIVE_CAMPAIGN_HTML = r"""
<section style="border:1px solid #415469;padding:16px;margin-top:20px;border-radius:8px">
<h2>Next campaign step — supervised execution</h2>
<p>Uses the exact vault and amount from the active campaign. Fresh simulation is mandatory. Each transaction requires a separate MetaMask confirmation.</p>
<button id="campaignQuote">Request next-step quote</button>
<button id="campaignAuthorize" disabled>Review and authorize campaign step</button>
<button id="campaignReceipt" disabled>Recheck campaign receipt</button>
<p>Recover a previously authorized transaction after a restart: enter the campaign quote ID and its Base transaction hash. This only checks the existing receipt.</p>
<label>Quote ID <input id="campaignRecoveryQuote" type="text" style="padding:8px"></label>
<label>Transaction hash <input id="campaignRecoveryHash" type="text" style="padding:8px"></label>
<button id="campaignRecover">Recover campaign transaction</button>
<pre id="campaignTxOutput">No campaign quote requested.</pre>
</section>
<script>
let campaignCurrentQuote=null;
let campaignLastTx=null;
let campaignSubmissionPhase='not_submitted';
const campaignOut=(value)=>document.getElementById('campaignTxOutput').textContent=value;
function campaignReceiptDisplay(receipt){
 if(receipt.status==='PENDING_NOT_FOUND' || receipt.status==='PENDING_MATCHED_INPUT')
  return 'TRANSACTION SUBMITTED; receipt not yet verified. Do not resend. Use Recheck campaign receipt.\\n'+JSON.stringify(receipt,null,2);
 return JSON.stringify(receipt,null,2);
}
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
  campaignSubmissionPhase='journaled_awaiting_wallet';
  await campaignVerifyWallet();
  const tx={from:ready.wallet,to:ready.to,data:ready.data,value:ready.value,gas:ready.gas};
  const hash=await window.ethereum.request({method:'eth_sendTransaction',params:[tx]});
  campaignSubmissionPhase='submitted_awaiting_receipt';
  campaignLastTx={quote_id:quoted.quote_id,tx_hash:hash,stage:ready.stage};
  document.getElementById('campaignReceipt').disabled=false;
  campaignOut('Submitted '+hash+'. Checking receipt...');
  const endpoint=ready.stage==='withdraw'?'/api/withdraw/check':'/api/deposit/check';
  const receipt=await campaignPost(endpoint,{quote_id:quoted.quote_id,tx_hash:hash});
  if(receipt.status==='RECEIPT_AND_BLOCK_MATCH')campaignSubmissionPhase='reconciled';
  campaignOut(campaignReceiptDisplay(receipt));
 }catch(e){
  if(campaignLastTx && campaignSubmissionPhase==='submitted_awaiting_receipt'){
   campaignOut('TRANSACTION SUBMITTED. Receipt check failed: '+e.message+'\\nTransaction: '+campaignLastTx.tx_hash+'\\nQuote: '+campaignLastTx.quote_id+'\\nDo not resend. Use Recheck campaign receipt or recovery.');
  }else if(campaignSubmissionPhase==='journaled_awaiting_wallet'){
   campaignOut('Authorization journaled, but wallet submission is unconfirmed. Check MetaMask activity before retrying. Quote: '+quoted.quote_id+'\\nDetails: '+e.message);
  }else{
   campaignOut('No transaction hash received by LVS. Check MetaMask activity before any retry. '+e.message);
  }
 }
};
document.getElementById('campaignRecover').onclick=async()=>{
 try{
  await campaignVerifyWallet();
  const quote_id=document.getElementById('campaignRecoveryQuote').value.trim();
  const tx_hash=document.getElementById('campaignRecoveryHash').value.trim();
  if(!/^[A-Za-z0-9_-]{20,100}$/.test(quote_id) || !/^0x[0-9a-fA-F]{64}$/.test(tx_hash))
   throw Error('Invalid quote ID or transaction hash');
  const details=await campaignPost('/api/campaign/recovery-info',{quote_id:quote_id});
  if(details.status!=='BOUND_AWAITING_RECEIPT')throw Error('Quote is not bound to an active campaign');
  campaignLastTx={quote_id,tx_hash,stage:details.stage};
  document.getElementById('campaignReceipt').disabled=false;
  const endpoint=details.stage==='withdraw'?'/api/withdraw/check':'/api/deposit/check';
  const checked=await campaignPost(endpoint,{quote_id,tx_hash});
  if(checked.status==='RECEIPT_AND_BLOCK_MATCH')campaignSubmissionPhase='reconciled';
  campaignOut(campaignReceiptDisplay(checked));
 }catch(e){campaignOut('Campaign recovery blocked: '+e.message)}
};
document.getElementById('campaignReceipt').onclick=async()=>{
 if(!campaignLastTx)return;
 try{
  await campaignVerifyWallet();
  const endpoint=campaignLastTx.stage==='withdraw'?'/api/withdraw/check':'/api/deposit/check';
  const checked=await campaignPost(endpoint,{
   quote_id:campaignLastTx.quote_id,tx_hash:campaignLastTx.tx_hash});
  if(checked.status==='RECEIPT_AND_BLOCK_MATCH')campaignSubmissionPhase='reconciled';
  campaignOut(JSON.stringify(checked,null,2));
 }catch(e){campaignOut('Campaign receipt check failed: '+e.message)}
};
</script>
"""
