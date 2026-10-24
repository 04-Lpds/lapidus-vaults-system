//! Read-only JSON-like RPC health probe. No wallet / signer / transaction logic.
use alloy::providers::{Provider, ProviderBuilder};
use std::error::Error;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let rpc_url = std::env::var("RPC_URL")?;
    let provider = ProviderBuilder::new().connect(&rpc_url).await?;
    let chain_id = provider.get_chain_id().await?;
    let latest_block = provider.get_block_number().await?;
    if let Ok(expected) = std::env::var("EXPECTED_CHAIN_ID") {
        let expected_chain_id: u64 = expected.parse()?;
        if chain_id != expected_chain_id {
            return Err(format!("Chain ID mismatch: got {}, expected {}", chain_id, expected_chain_id).into());
        }
    }
    println!("{{\"chain_id\":{},\"latest_block\":{}}}", chain_id, latest_block);
    Ok(())
}
