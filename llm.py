import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_llm(temperature: float = 0):
    """OpenRouter exposes an OpenAI-compatible API, so ChatOpenAI works with a different base_url."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("MODEL")
    if not api_key or not model:
        raise SystemExit("Set OPENROUTER_API_KEY and MODEL in .env (see .env.example).")
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
    )


TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


def is_transient(e: Exception) -> bool:
    """True for errors worth retrying: timeouts, rate limits, provider outages."""
    import openai

    if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError,
                      openai.RateLimitError, openai.InternalServerError)):
        return True
    # OpenRouter sometimes returns HTTP 200 with an error in the body, e.g.
    # {'message': 'Provider timed out', 'code': 504}. langchain-openai raises that as ValueError.
    if isinstance(e, ValueError) and e.args and isinstance(e.args[0], dict):
        return e.args[0].get("code") in TRANSIENT_CODES
    return False


def invoke_with_retry(runnable, messages, attempts: int = 4, base_delay: float = 2.0):
    """Call the model, retrying transient errors with backoff (2s, 4s, 8s).
    Non-transient errors (bad request, wrong key) are raised immediately."""
    import time

    for i in range(attempts):
        try:
            return runnable.invoke(messages)
        except Exception as e:
            if i == attempts - 1 or not is_transient(e):
                raise
            delay = base_delay * 2 ** i
            print(f"  [retry] {e} -- retrying in {delay:.0f}s ({i + 1}/{attempts - 1})")
            time.sleep(delay)
