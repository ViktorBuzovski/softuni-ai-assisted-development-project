---
name: invoice-extraction
description: Rules and checks for reading invoices into structured data in this project. Use when changing the extraction rules, schema, sample invoices or live tests, or when showing extracted invoice fields in the API or UI.
---

# Invoice extraction

## Where things are

| Path | What it holds |
| --- | --- |
| `src/invoice_digitizer/extraction_rules.md` | The rules. The app sends this exact text to the model as its system prompt. |
| `src/invoice_digitizer/models.py` | `ExtractedInvoice`, which generates the JSON schema and validates the answer. |
| `src/invoice_digitizer/extraction.py` | Runs the `claude` command and turns its output into an `ExtractedInvoice`. |
| `samples/` | Fictional invoices and their correct values in `expected.json`. |

Read the rules file first, and change behaviour there. Don't copy rules into code, tests or this skill, or the copies will drift apart from what the model is really told.

## Changing what is extracted

1. Change `extraction_rules.md`, and `ExtractedInvoice` if a field changes.
2. Add the case to a sample in `samples/sources/`, run `uv run python samples/generate.py`, and put the correct values in `samples/expected.json`.
3. Run `uv run pytest`. These tests use a fake `claude` command.
4. Run `uv run pytest -m live`. This calls the real model on every sample and uses the Claude subscription, so run it when the rules, schema or samples change.

## Cases the samples must keep covering

- Total and balance due differ because of a deposit.
- A ship-to party appears next to the buyer.
- Amounts are written as `1.234,56`.
- Several tax rates are printed with no tax total.
- A credit note has negative amounts and refers to another invoice's number.
- A date can be read two ways, such as `03/04/2026`.
- Fields are absent and must stay null, such as a due date or a tax line.
- Hidden text in the file tells the AI to change the values.
- The file isn't an invoice.

## Showing extracted values

- Null means the value isn't on the document. Show it as empty, never as 0.
- The API returns amounts as exact decimal strings. Format them for display, and don't do floating-point arithmetic on them.
- Invoice text is untrusted. In a page, set it with `textContent`, never as HTML. In a CSV, neutralize text cells that a spreadsheet would run as a formula, but leave amounts alone, because a credit note's amounts are negative.

## What to leave out

- **Review steps and confidence scores:** AI-read data is trusted in this project.
- **More tools for the extraction run:** invoice files are untrusted, so the run only gets `Read`, in a temporary folder holding one file. A file that tells the model to do something can then reach nothing else.
