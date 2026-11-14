# W7 A17 — Put an LLM behind your API

This API classifies one customer support message into a closed category and urgency, with a confidence score and one-sentence reason. It is a single request and response, not a chatbot. Pydantic validates both the input and every model response before returning it.

## Run in stub mode

Use Python 3.10 or newer from this folder:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn main:app --reload
```

The committed `.env.example` sets `LLM_STUB=1`, so the API works without provider access or model calls. It is preconfigured for OpenAI GPT-6 Luna once you add an API key and turn stub mode off. Open <http://localhost:8000/docs>.

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

Expected error body: `{"error":"Field 'text' is required"}`. Capture actual output from your own run for the final submission.

## Enable a real provider

The example values target OpenAI's API using the `gpt-6-luna` model. Add your OpenAI API key to the ignored `.env`, set `LLM_STUB=0`, and keep `LLM_ENABLED=true`. The three provider settings are `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`; do not commit `.env` or real keys. Luna supports this project's Chat Completions call. The prompt lives in `prompts/triage-v1.md`, separate from user content.

Set `LLM_ENABLED=false` to use a deterministic human-review fallback without making a model call. Each real request attempt prints a structured token, model, duration, prompt-version, retry, and status log. If a response is malformed or violates the schema, it receives one repair call; a second failure is quarantined and returns 422. Raw model text is never returned to the caller.

## Evaluation and submission evidence

`evals/cases.json` contains eight hand-labeled examples, including ambiguous input. After configuring a provider and starting the server, run `python run_eval.py`. Add the actual score, date, and prompt version below; do not report an unrun or stub score as a real model evaluation.

- Provider/model used: fill in after a real run.
- Eval result (date and `triage-v1`): fill in after `python run_eval.py`.
- One call's actual usage/cost: record a real structured log line and provider rate.
- Estimated cost for 10,000 requests/day: calculate from measured input/output tokens and the selected provider's current rates; local Ollama has no provider token charge, but uses local compute.
- What I would fix with another day: fill in after inspecting the evaluation failures.

The API key, real provider run, honest eval score, cost estimate, and public GitHub publication are personal submission steps. Never put real personal, confidential, or employer data into a free model endpoint.

The versioned system prompt is in `prompts/triage-v1.md`; the eight hand-labeled cases are in `evals/cases.json`. The evaluation runner scores category accuracy and prints each mismatch. No real-provider score is recorded yet: run it with your provider settings and add the observed score, date, cost log, and cost estimate above. Stub-mode results are not a real model evaluation.
