"""AI integrations for the client — the LLM-backed bot strategy and its transport.

Lives in client/ because it needs the network, which core/ may not import;
the strategy implements core's BotStrategyInterface so the dependency arrow
stays client -> core. Provider choice (Groq, OpenAI, ...) is a registry entry
in providers.py plus env configuration — no other module names a vendor.
"""
