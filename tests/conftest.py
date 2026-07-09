"""
Shared fixtures and environment setup for all tests.
All tests run without a real blockchain connection or Anthropic API key.
"""
import os
import sys

# Ensure the repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Provide minimal environment so config.py doesn't fail on missing vars
os.environ.setdefault("PRIVATE_KEY", "0x" + "a" * 64)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BASE_RPC_URL", "https://localhost:9999")  # won't be called
os.environ.setdefault("HL_PRIVATE_KEY", "")
