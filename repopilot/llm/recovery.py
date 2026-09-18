import time


def resilient_chat(create, params, sleep=time.sleep, attempts=3, fallback_model=None):
    request = dict(params)
    for attempt in range(attempts):
        try:
            response = create(**request)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            connection_error = type(exc).__name__ in ("APIConnectionError", "APITimeoutError")
            if not connection_error and status not in (429, 500, 502, 503, 529) or attempt == attempts - 1:
                raise
            if status in (503, 529) and fallback_model:
                request["model"] = fallback_model
            sleep(.5 * 2 ** attempt)
            continue
        if response.choices[0].finish_reason != "length":
            return response
        if attempt == attempts - 1:
            raise RuntimeError("Model response repeatedly truncated; no incomplete tool call was executed")
        request["max_tokens"] = min(request.get("max_tokens", 4000) * 2, 16000)
    raise RuntimeError("Model request did not complete")
