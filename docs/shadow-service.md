# Stage 1: read-only Base shadow service

This service continuously observes the configured dedicated Base wallet, evaluates 7-day historical Morpho APY versus estimated fees, and stores decision evidence in its own SQLite journal. It never reads LVS_AUTO_PRIVATE_KEY, creates a campaign, or signs/sends a transaction.

It stops on Ctrl+C, data/STOP_AUTOMATION, data/STOP_SHADOW_SERVICE, or three consecutive failed monitoring cycles. Default interval is 900 seconds and default session length is 96 cycles (approximately one day). Restart explicitly after the session ends; it is not yet an operating-system service or a live trading daemon.

## Windows / PyCharm PowerShell

Set BASE_RPC_URL to a trusted HTTPS Base RPC provider in your current shell. Do not paste credentials or RPC tokens into bug reports, screenshots or Git commits. For example, if BASE_RPC_URL was already configured in your current environment:

```powershell
git pull
Test-Path Env:BASE_RPC_URL
.\.venv\Scripts\python.exe -m lapidus_vaults.operator shadow-service --wallet 0x3E62eEC622a1db01324A32e7837fb2eaE33fB0E3 --allowlist data/paper_test_allowlist.json --db data/lvs_shadow_service.sqlite3 --eth-usd 3000 --interval-seconds 900 --max-cycles 96
```

The ETH/USD parameter is an operator assumption used for gas economics; it is not a live price feed. With only a few dollars of USDC this should generally HOLD.

Do **not** reactivate the older Windows scheduled task `Lapidus Vault Systems - Read Only Monitor`; that task belongs to the previous monitoring implementation. Do not launch two shadow services on the same database. The monitor uses a local exclusive .monitor.lock file and fails closed if it already exists. Never blindly delete a lock while another process may be running.

To request a clean stop, create data/STOP_SHADOW_SERVICE; the process checks between cycles. To resume, remove the stop file after verifying that the service has exited. Ctrl+C is immediate between network operations. The optional STOP_AUTOMATION file also stops the shadow service.

```powershell
New-Item -ItemType File -Path data/STOP_SHADOW_SERVICE
# Once the service has stopped:
Remove-Item data/STOP_SHADOW_SERVICE
```

A visible PowerShell window is intentional in this phase. System restart persistence, machine boot scheduling, alert delivery and autonomous live campaign signing are not included. The service is only as available as the Windows computer, network, RPC provider and user session.
