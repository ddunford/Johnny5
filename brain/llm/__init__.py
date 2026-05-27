"""The LLM router and inference clients.

Every cognitive subsystem reaches inference *only* through this layer (FC-4):
the router picks a provider per cognitive role, enforces circuit breaking and
retry-with-feedback, falls back down the chain ("tired" degradation), and logs
every call with cost to `llm_call_log`. No inner agent calls a provider directly.
"""
