# W7 A17 — Put an LLM behind your API

I built this API to classify one customer support message into a category and urgency, with a confidence score and a short reason. It handles one request at a time rather than acting as a chatbot. I use Pydantic to validate the request and the model's response.

## Run in stub mode

From this project folder, I set it up with Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn main:app --reload
```

I leave `LLM_STUB=1` in `.env.example` so the API can be started without making a provider call. To try the real model, I add my OpenAI API key to `.env` and set `LLM_STUB=0`. I can then open <http://localhost:8000/docs> to try the endpoint.

## Contract

`POST /triage` accepts `{"text":"..."}` with 1–2000 non-whitespace characters. The response always has `category` (`billing`, `bug`, `feature`, `other`), `urgency` (`low`, `normal`, `high`), `confidence` (0–1), and `reason` (one short sentence). Invalid input returns 400 naming `text`; a model answer that fails schema validation after one repair returns 422 and is written to `logs/quarantine.jsonl`. Provider timeout returns 504. The model call has a 30-second timeout; only timeouts, 429, and 5xx errors are retried, with exponential backoff and jitter. The OpenAI SDK's own retries are disabled.

## Stub checkpoint

```bash
curl -i -X POST http://localhost:8000/triage -H 'Content-Type: application/json' -d '{"text":"The app crashes when I sign in."}'
```

With `LLM_STUB=1`, the deterministic response body is:

```json
{"category":"other","urgency":"normal","confidence":0.5,"reason":"Stub mode returns a fixed example response."}
```

Invalid input checkpoint:

```bash
curl -i -X POST http://localhost:8000/triage -H 'Content-Type: application/json' -d '{}'
```

Expected error body: `{"error":"Field 'text' is required"}`.

## Enable a real provider

I configured the example for OpenAI's API and the `gpt-6-luna` model. In my ignored `.env`, I set `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`, then set `LLM_STUB=0` and keep `LLM_ENABLED=true`. I don't commit `.env` or my API key. The system prompt is in `prompts/triage-v1.md`, separate from the support message being classified.

If I set `LLM_ENABLED=false`, the API returns a human-review fallback without calling the model. For a real request, the app logs the model, token counts, duration, prompt version, retry attempt, and status. If the model returns malformed data, the app tries one repair request; if that also fails validation, it saves the rejected result to quarantine and returns 422. It never returns raw model text to the API caller.

## Evaluation and submission evidence

I tested `POST /triage` with GPT-6 Luna. I saved the terminal result in the screenshot below. I keep the evaluation score blank until I run the eight cases in `evals/cases.json` with the real provider using `python run_eval.py`.

/home/pranav/Pictures/Screenshots/Screenshot_20261114_140858.png 

- Provider/model I tested: OpenAI GPT-6 Luna (`gpt-6-luna`).
- Eval result (date and `triage-v1`): add after I run `python run_eval.py`.
- One call's actual usage/cost: add the usage from a real request and the provider's rate.
- Estimated cost for 10,000 requests/day: calculate this from measured input/output tokens and the selected provider's current rates.
- What I would fix with another day: add after I review the evaluation results.

I use made-up support messages while testing and never put real personal, confidential, or employer data into a free model endpoint. I keep my API key private.

The prompt is versioned in `prompts/triage-v1.md`. The evaluation runner checks category accuracy across eight examples and prints any mismatches. Stub-mode results don't count as a real model evaluation.
