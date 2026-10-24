# The Rust worker, explained for a Python developer

**File:** `rust/chain-worker/src/main.rs` (read it alongside this guide). The Rust code is intentionally small: a read-only RPC connectivity check, not a capital allocator.

1. `use alloy::providers::{Provider, ProviderBuilder};` imports the Alloy provider builder and the `Provider` trait. In Python terms, these provide a connection object and methods on it; importing the trait makes its RPC methods available.
2. `use std::error::Error;` brings Rust's common error interface into scope. `Box<dyn Error>` lets this tiny prototype return various error types without defining its own error enum.
3. `#[tokio::main]` constructs an asynchronous Tokio runtime for the `async fn main` entry point (roughly `asyncio.run(main())` in Python).
4. `async fn main() -> Result<(), Box<dyn Error>>` means `main` returns `Ok(())` on success or an error on failure. `()` is Rust's 'unit' value (roughly Python `None`).
5. `std::env::var("RPC_URL")?` reads an environment variable; `?` short-circuits to the caller if it fails, like raising/propagating an exception.
6. `ProviderBuilder::new().connect(&rpc_url).await?` creates an Alloy RPC provider and awaits initialization. `&rpc_url` borrows the string rather than transferring ownership.
7. `provider.get_chain_id().await?` and `provider.get_block_number().await?` perform network reads. They DO NOT send a transaction.
8. The optional `EXPECTED_CHAIN_ID` branch parses a `u64` (unsigned 64-bit integer) and returns an error if the RPC is on the wrong chain. That is the main safety check in this prototype.
9. `println!(...)` prints a JSON-shaped line with the observed chain ID and block. This is formatted manually; a proper structured runtime should use a JSON serializer and typed messages.
10. `Ok(())` tells Rust the program finished successfully.

`Cargo.toml` is the equivalent of a project dependency/configuration manifest (`pyproject.toml` in Python). This worker uses `alloy` for Ethereum JSON-RPC and `tokio` for asynchronous execution. `cargo check` checks type correctness and dependency compatibility without producing the normal optimized executable.

## What we do NOT want to do yet

Do not introduce private keys, chain approvals, user funds, or trading operations while learning the basic code. The next Rust exercise should be another **read-only** call (for example a known ERC-20 `decimals()` or `balanceOf()`), with explicit contract address validation and a test. Then we can add typed messages shared with the Python decision engine. Signing will come only after a full execution/safety design and fork tests.
