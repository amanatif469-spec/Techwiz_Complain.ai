import json

from sqlalchemy.orm import Session

from database.models import Complaint, GenAIRun
from genai_pipeline.client import GenAIError, generate_structured, provider_chain
from knowledge_base.retrieval import retrieve_policy_chunks
from prompt_templates.loader import PROMPT_NAME, active_prompt_version, render_prompts
from security.pii import mask_pii
from security.prompt_injection import wrap_untrusted_complaint, wrap_untrusted_policy


def run_genai_pipeline(
    db: Session,
    complaint: Complaint,
    *,
    categories: list[str],
    departments: list[str],
    taxonomy: str = "",
    tone: str = "professional",
    repeat_context: str = "",
) -> tuple[GenAIRun, list[dict]]:
    policy_chunks = retrieve_policy_chunks(db, f"{complaint.title} {complaint.description} {complaint.product_or_service}")
    prompt_version = active_prompt_version()
    # Customer text and uploaded documents are untrusted; PII is masked before it leaves the app.
    complaint_text = mask_pii(f"{complaint.title}\n{complaint.description}")
    system_prompt, user_prompt = render_prompts(
        {
            "complaint_id": complaint.complaint_code,
            "customer_type": complaint.customer_type.value,
            "channel": complaint.channel.value,
            "product_or_service": complaint.product_or_service,
            "order_reference": complaint.order_reference,
            "requested_resolution": mask_pii(complaint.requested_resolution or ""),
            "previous_complaint_reference": complaint.previous_complaint_reference,
            "repeat_context": repeat_context,
            "wrapped_complaint": wrap_untrusted_complaint(complaint_text),
            "policy_chunks": [{**chunk, "content": wrap_untrusted_policy(chunk["content"])} for chunk in policy_chunks],
            "policy_codes": sorted({chunk["document_code"] for chunk in policy_chunks}),
            "categories": ", ".join(categories),
            "departments": ", ".join(departments),
            "taxonomy": taxonomy,
            "tone": tone,
        },
        version=prompt_version,
    )
    policy_versions = [
        {"document_code": c["document_code"], "version": c["version"], "status": c["status"]} for c in policy_chunks
    ]
    try:
        result = generate_structured(system_prompt, user_prompt)
        run = GenAIRun(
            complaint_id=complaint.id,
            provider=result["provider"],
            model=result["model"],
            prompt_name=PROMPT_NAME,
            prompt_version=prompt_version,
            policy_versions=policy_versions,
            attempt=result["attempt"],
            latency_ms=result["latency_ms"],
            raw_response=result["raw"],
            structured_output=result["structured"],
            is_valid_schema=True,
            error_message="",
        )
    except GenAIError as exc:
        attempted = provider_chain()
        run = GenAIRun(
            complaint_id=complaint.id,
            provider=",".join(attempted) or "unconfigured",
            model="",
            prompt_name=PROMPT_NAME,
            prompt_version=prompt_version,
            policy_versions=policy_versions,
            attempt=exc.attempts,
            latency_ms=0,
            # No model answer to keep, so the column holds the retry evidence instead.
            raw_response=json.dumps({"failure": str(exc)}),
            structured_output={},
            is_valid_schema=False,
            error_message=str(exc),
            routed_to_manual_review=True,
        )
    db.add(run)
    db.flush()
    return run, policy_chunks
