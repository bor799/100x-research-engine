# Timeout Candidate

This fixture has normal article content, but the LLM stub will simulate a provider
timeout during scoring. The queue should move to retry_scheduled rather than terminal
failure.
