# AI tool usage declaration

This file satisfies SRS section 1.8 / deliverable 18.

| Tool | Purpose | Assistance requested | Files affected | Changes made | Tests performed | Verifying team members |
|---|---|---|---|---|---|---|
| Cursor Grok 4.6 | Implement FastAPI + PostgreSQL backend from the SupportNova SRS | Scaffold API, models, dual pipelines, validation, docs | `src/`, `database/`, `genai_pipeline/`, `python_validation/`, `complaint_rules/`, `routing_rules/`, `escalation_rules/`, `prompt_templates/`, `schemas/`, `comparison_engine/`, `hallucination_checks/`, `security/`, `document_processing/`, `knowledge_base/`, `complaint_processing/`, `tests/`, `README.md` | Original NimbusCarta domain data, rule matrix, prompts, and review/audit flow written and adapted; not a paste of a third-party ticketing product | `pytest` unit tests for validation, injection, schema, comparison, priority traps | Team to re-run, review, and sign |
| Cursor | Build the responsive React frontend and connect it to the FastAPI API | Vite setup, Zustand state, role-based UX, dashboards and workflow integration | `frontend/`, `README.md` | Added login, role navigation, complaint submission/detail/analysis, manual review, knowledge uploads, analytics, reports and responsive design system | `npm run build`; `npm run lint` | Team to review and sign |
| Claude Code (Anthropic) | Review backend/frontend integration against the SRS and fix defects | Code review, bug fixes, SRS gap analysis, tests | `complaint_rules/`, `escalation_rules/`, `routing_rules/`, `python_validation/`, `genai_pipeline/`, `hallucination_checks/`, `knowledge_base/`, `complaint_processing/`, `security/prompt_injection.py`, `prompt_templates/` (new v2), `database/seed.py`, `src/`, `frontend/src/`, `tests/`, `README.md` | Whole-word keyword matching (fixed false legal/media escalations), primary/secondary issue detection, configurable priority table, policy applicability/version checks, GenAI schema-retry with time budget, GenAI failure routed to review, customer-safe API responses, VIP self-promotion blocked, review queue clears after a decision, upload hardening, real (non-fabricated) metrics, report types, admin configuration UI, status/assign/filter/history UI | `pytest` (36 unit + 42 DB integration tests), `npm run build`, manual browser check of each role | Team to review and sign |

Notes:

- The Generative AI API used at runtime (OpenAI / Gemini / Anthropic) is part of Pipeline 1 architecture, not a substitute for Python rules.
- Team members must be able to explain every module. Edit this table as additional AI assistance occurs.
