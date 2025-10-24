import os
import time
import requests
from typing import Optional
from perplexity import Perplexity
import LinkedAuth


# simple in-process cache (ticker+days) -> (ts, summary)
_CACHE = {}
_TTL_SEC = int(os.getenv("PPLX_CACHE_TTL", "900"))  # 15 minutes

def summarize_ticker_news_with_perplexity(ppx_api, ticker: str, days: int = 1, timeout: int = 30, model: Optional[str] = None) -> str:
        
    """
    One-call search + summarize for recent ticker news using Perplexity Sonar (online) models.
    ppx_api: API key string, dict from your vault, or rely on env PPLX_API_KEY.
    """
    print(f"Generating Perplexity summary for {ticker} over last {days} day(s)...")
    
    api_key = LinkedAuth.get_creds("spectral-nature-kvault", retreive = ['PerplexityAPI'])[0]

    if not api_key:
        return "Perplexity summary unavailable (missing PPLX_API_KEY)."

    mdl = "llama-3.1-sonar-large-128k-online"
    cache_key = f"{ticker}:{days}:{mdl}"
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key][0] < _TTL_SEC:
        return _CACHE[cache_key][1]

    client = Perplexity(api_key=api_key, timeout=timeout)

    system = "You are a finance analyst. Search the web and summarize recent, material company news with citations."
    user = (
        f"Summarize the most impactful news for {ticker} from the last {days} day(s). "
        "Provide 5-8 concise bullets with links/citations. Focus on catalysts, earnings, guidance, regulatory, legal, M&A, macro, and why it matters."
    )

    try:
        resp = client.chat.completions.create(
            model='sonar',
            temperature=0.3,
            max_tokens=700,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()

        print(content)

        if not content:
            content = "No summary content returned."
        _CACHE[cache_key] = (now, content)
        return content
    except Exception as e:
        return f"Perplexity summary error: {e}"