# 24. Document AI Form Parser + Invoice Parser

> Extract structured key-value pairs, tables, and invoice entities from PDF documents using Document AI specialized processors. **GCP Services:** Document AI (Form Parser, Invoice Parser)

> **Requires Document AI API enabled and processor IDs. Run `deploy.sh` first, then create processors in the GCP Console.**

## Architecture

```
[Form PDF]                          [Invoice PDF]
     |                                   |
     v                                   v
[form_parser.py]                [invoice_parser.py]
FORM_PARSER_PROCESSOR           INVOICE_PROCESSOR
     |                                   |
     v                                   v
Key-Value Pairs (confidence)    Headline Entities:
Tables (header + body rows)       supplier_name
                                  invoice_id
                                  invoice_date
                                  due_date
                                  total_amount
                                  currency
                                  net_amount
                                  total_tax_amount
                                Nested Line Items:
                                  description
                                  quantity
                                  unit_price
                                  amount
```

## What this demonstrates

- Form Parser: extracts key-value pairs from arbitrary form layouts without training; returns confidence scores per field; handles tables with header and body rows
- Invoice Parser: extracts structured invoice entities from supplier invoices; handles nested line-item properties (description, quantity, price)
- Confidence score thresholding: headline field identification for AP automation integration
- Error handling for missing API enablement and missing processor IDs with clear remediation steps
- Separation of processor types: Form Parser and Invoice Parser are distinct processors with different entity schemas

## Prerequisites

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

Authenticate:
```bash
gcloud auth application-default login
```

> **Note:** Document AI API must be enabled before running. Each parser requires its own processor ID — the Form Parser and Invoice Parser are distinct processor types.

## Setup

```bash
chmod +x deploy.sh && ./deploy.sh
```

Then create the processors in the GCP Console:
- Document AI > Create Processor > **Form Parser** → copy the processor ID
- Document AI > Create Processor > **Invoice Parser** → copy the processor ID

```bash
export DOCAI_FORM_PROCESSOR_ID=your-form-parser-id
export DOCAI_INVOICE_PROCESSOR_ID=your-invoice-parser-id
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run both parsers with sample PDFs:
python main.py

# Run form parser only:
python main.py --mode form

# Run invoice parser only:
python main.py --mode invoice

# Use your own PDF:
python main.py --mode form --pdf data/my-form.pdf
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | Orchestrates both parsers; --mode flag selects form, invoice, or both |
| `form_parser.py` | FORM_PARSER_PROCESSOR — key-value pairs and table extraction |
| `invoice_parser.py` | INVOICE_PROCESSOR — structured invoice entity extraction with line items |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | Enable Document AI API and print processor creation instructions |

---
Built by [TechTrapture](https://techtrapture.com) | [Academy](https://academy.techtrapture.com) | [YouTube](https://www.youtube.com/@TechTrapture) | vishal.bulbule@techtrapture.com
