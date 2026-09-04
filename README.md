# SDR Agent

CrewAI flow that researches a prospect and drafts a **cold**, **offer**, or **update** email. Drafts are written to `output/`. Mail is sent only if you set `SEND_EMAILS=true` and SMTP credentials.

The language model never sends mail on its own. The Flow delivers after the draft is saved.

## What you get

1. Prospect research brief (web search/scrape when `SERPER_API_KEY` is set)
2. One personalized email with subject, body, signature, mailing address, and opt-out line
3. Files in `output/` for review
4. Optional SMTP send

## Setup

Python 3.10–3.13. From this directory:

```bash
crewai install
cp .env.example .env
```

Put your `OPENAI_API_KEY` in `.env`. Add `SERPER_API_KEY` if you want live company research.

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
