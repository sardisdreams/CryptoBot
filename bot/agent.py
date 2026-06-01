import json
import anthropic
from bot.config import ANTHROPIC_API_KEY, TOKENS
from bot.portfolio import Portfolio
from bot.executor import Executor
from bot.logger import setup_logger

logger = setup_logger("agent")

SYSTEM_PROMPT = """You are an autonomous crypto trading agent operating on the Base blockchain.

Your job is to analyze the current portfolio and market conditions, then decide whether to make trades.

Guidelines:
- Preserve capital — only trade when there is a clear opportunity
- Never risk more than 10% of total portfolio value in a single trade
- Keep at least 0.005 ETH in reserve for gas fees at all times
- Prefer stable positions (USDC) when market conditions are uncertain
- Diversify across multiple tokens when there is conviction
- Consider current allocations — avoid over-concentration in any single asset

When you decide to trade, use the execute_swap tool. You may call it multiple times for multiple trades.
If no action is warranted, call no tools and explain your reasoning briefly.

Always reason step by step before acting."""

TOOLS = [
    {
        "name": "execute_swap",
        "description": "Execute a token swap on Uniswap V3 on Base chain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "token_in": {
                    "type": "string",
                    "description": "Symbol of the token to sell (e.g. WETH, USDC, cbBTC)",
                },
                "token_out": {
                    "type": "string",
                    "description": "Symbol of the token to buy",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "USD value of the trade",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are making this trade",
                },
            },
            "required": ["token_in", "token_out", "amount_usd", "reasoning"],
        },
    }
]


class TradingAgent:
    def __init__(self, portfolio: Portfolio, executor: Executor):
        self.portfolio = portfolio
        self.executor = executor
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def _build_market_prompt(self, snapshot: dict) -> str:
        lines = [
            f"Total portfolio value: ${snapshot['total_usd']:,.2f}",
            "",
            "Current holdings:",
        ]
        for symbol, h in snapshot["holdings"].items():
            if h["balance"] > 0:
                lines.append(
                    f"  {symbol}: {h['balance']:.6f} units | "
                    f"${h['value_usd']:,.2f} | {h['allocation_pct']:.1f}% of portfolio | "
                    f"Price: ${h['price_usd']:,.4f}"
                )

        lines += ["", "Current market prices (USD):"]
        for symbol, price in snapshot["prices"].items():
            lines.append(f"  {symbol}: ${price:,.4f}")

        lines += [
            "",
            "Available tokens for trading:",
            ", ".join(TOKENS.keys()),
        ]
        return "\n".join(lines)

    def _execute_tool(self, tool_input: dict, snapshot: dict) -> str:
        token_in_sym = tool_input["token_in"].upper()
        token_out_sym = tool_input["token_out"].upper()
        amount_usd = float(tool_input["amount_usd"])
        reasoning = tool_input.get("reasoning", "")

        logger.info(f"Agent decision: {token_in_sym} → {token_out_sym} | ${amount_usd:.2f} | {reasoning}")

        token_in = TOKENS.get(token_in_sym)
        token_out = TOKENS.get(token_out_sym)

        # ETH (native) maps to WETH for routing
        if token_in_sym == "ETH":
            token_in = TOKENS["WETH"]
            token_in_sym = "WETH"
        if token_out_sym == "ETH":
            token_out = TOKENS["WETH"]
            token_out_sym = "WETH"

        if not token_in or not token_out:
            return f"Error: unknown token symbol. Available: {', '.join(TOKENS.keys())}"

        price_in = snapshot["prices"].get(token_in_sym, 0)
        if price_in == 0:
            return f"Error: could not get price for {token_in_sym}"

        # Convert USD amount to token units
        amount_tokens = amount_usd / price_in
        amount_wei = int(amount_tokens * (10 ** token_in["decimals"]))

        # Check available balance
        holding = snapshot["holdings"].get(token_in_sym, {})
        available_usd = holding.get("value_usd", 0)
        if amount_usd > available_usd * 0.98:
            return f"Insufficient balance: have ${available_usd:.2f} of {token_in_sym}, need ${amount_usd:.2f}"

        tx_hash = self.executor.swap(
            token_in_address=token_in["address"],
            token_in_symbol=token_in_sym,
            token_in_decimals=token_in["decimals"],
            token_out_address=token_out["address"],
            token_out_symbol=token_out_sym,
            amount_in_wei=amount_wei,
            price_eth_usd=snapshot["prices"].get("WETH", 0),
        )

        if tx_hash:
            return f"Swap submitted: {tx_hash}"
        return "Swap failed — check logs"

    def run_once(self):
        logger.info("Agent tick: fetching portfolio snapshot...")
        snapshot = self.portfolio.get_snapshot()
        market_context = self._build_market_prompt(snapshot)

        messages = [{"role": "user", "content": market_context}]

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Agentic loop — handle tool calls until model stops
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = self._execute_tool(block.input, snapshot)
                    logger.info(f"Tool result: {result}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            response = self.client.messages.create(
                model="claude-opus-4-8",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

        # Log final reasoning
        for block in response.content:
            if hasattr(block, "text"):
                logger.info(f"Agent reasoning: {block.text}")
