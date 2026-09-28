# Backend architecture notes

SupportNova keeps two pipelines separate:

1. `genai_pipeline` loads versioned Jinja prompts, wraps the complaint as untrusted data, retrieves policy chunks, and asks the model for schema-constrained JSON.
2. `python_validation` never calls a GenAI API. It classifies from `resolution_rules`, enforces `escalation_rules`, checks refund/replacement/compensation eligibility, schema, hallucinations, and prohibited actions.

`comparison_engine` scores field agreement. Disagreement, missing policy support, injection, or mandatory escalation sends the ticket to `review_actions` while `audit_log` keeps the original recommendation.

Policy precedence: active Policy > SOP > FAQ; newer effective date wins; superseded/draft/expired policies are not used as the primary resolution basis.
