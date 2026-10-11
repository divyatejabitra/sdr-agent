# SDR Agent

CrewAI flow that researches a prospect and drafts a **cold**, **offer**, or **update** email. Drafts are written to `output/`. Mail is sent only if you set `SEND_EMAILS=true` and SMTP credentials.

The language model never sends mail on its own. The Flow delivers after the draft is saved.

## What you get

1. Prospect research brief (web search/scrape when `SERPER_API_KEY` is set)
2. Three drafts written in parallel in different voices (direct, consultative, curious) — each on a genuinely different underlying model (OpenAI, Anthropic, Google Gemini) when those keys are set, not just three prompts on one model, for real diversity at no extra LLM-call cost
3. A judge agent that scores the drafts and picks one, with a short rationale
4. The winning email with subject, body, signature, mailing address, and opt-out line
5. Files in `output/` for review (the draft records which voice won and why)
6. Optional SMTP send
7. A free, zero-LLM-cost compliance check before any of the above runs — a lead missing sender identity, physical address, or a recipient email fails fast instead of burning research + drafts + judge on a lead that can't produce a compliant email anyway

Drafting three variants and judging them costs roughly three times the drafting LLM calls of a single draft, plus one judge call, in exchange for picking the best of three instead of accepting the first.

## Setup

Python 3.10–3.13. From this directory:

```bash
crewai install
cp .env.example .env
```

Put your `OPENAI_API_KEY` in `.env`. Add `SERPER_API_KEY` if you want live company research. Add `ANTHROPIC_API_KEY` and/or `GOOGLE_API_KEY` for genuine cross-model draft diversity — each is independently optional; a voice whose key is missing falls back to OpenAI rather than failing. `GOOGLE_API_KEY` works whether it's a classic AI Studio API key or an Auth key — see `.env.example` for why.

Edit `data/sample_lead.json` (or point `LEADS_FILE` at your own JSON) with a real prospect you are allowed to contact, your product, and your physical mailing address.

## Run

```bash
crewai run
```

Campaign type comes from the lead file: `cold`, `offer`, or `update`.

Batch three sample campaign types:

```bash
LEADS_FILE=data/sample_leads.json uv run python -c "from sdr_agent.main import run_batch; run_batch()"
```

Plot the flow:

```bash
uv run python -c "from sdr_agent.main import plot; plot()"
```

## Sending mail

1. Review the markdown draft in `output/`
2. Set `SEND_EMAILS=true` plus `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`
3. Run again only for leads you are allowed to email

To confirm each send by hand, also set `REQUIRE_APPROVAL=true`. The Flow then prints the winning draft and asks `Send this email? [y/N]` before delivering. Anything other than `y`/`yes` keeps it as a draft. If there is no interactive terminal (for example `run_batch` or `run_with_trigger`), it does not send and does not wait for input.

Use addresses you own while testing. Follow CAN-SPAM / CASL and your own suppression list. This project does not scrape or buy contact lists.

## Lead JSON fields

| Field | Purpose |
| --- | --- |
| `campaign_type` | `cold`, `offer`, or `update` |
| `prospect_*` | Who you are writing to |
| `sender_*` / `physical_address` | Who you are and required mailing address |
| `product_name` / `value_proposition` | What you sell |
| `offer_details` | Used for offer campaigns |
| `update_details` | Used for update campaigns |
| `extra_notes` | Facts the researcher and SDR may use |
