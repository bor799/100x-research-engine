# Rate Limited Candidate

This fixture has normal article content, but the LLM stub will simulate a provider
rate limit during scoring. The queue should move to retry_scheduled with a concrete
next retry timestamp.
