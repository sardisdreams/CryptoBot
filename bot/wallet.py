from web3 import Web3
from eth_account import Account
from bot.config import PRIVATE_KEY
from bot.logger import setup_logger

logger = setup_logger("wallet")


class Wallet:
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.account = Account.from_key(PRIVATE_KEY)
        self.address = self.account.address
        logger.info(f"Wallet loaded: {self.address}")

    def get_eth_balance(self) -> float:
        balance_wei = self.w3.eth.get_balance(self.address)
        return self.w3.from_wei(balance_wei, "ether")

    def get_token_balance(self, token_address: str, decimals: int = 18) -> float:
        erc20_abi = [
            {"inputs": [{"name": "account", "type": "address"}],
             "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
             "stateMutability": "view", "type": "function"},
        ]
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=erc20_abi,
        )
        raw = token.functions.balanceOf(self.address).call()
        return raw / (10 ** decimals)

    def get_nonce(self) -> int:
        return self.w3.eth.get_transaction_count(self.address, "pending")

    def sign_and_send(self, tx: dict) -> str:
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"Transaction sent: {tx_hash.hex()}")
        return tx_hash.hex()

    def wait_for_receipt(self, tx_hash: str, timeout: int = 120):
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
        status = "success" if receipt["status"] == 1 else "failed"
        logger.info(f"Transaction {tx_hash} {status} (block {receipt['blockNumber']})")
        return receipt
