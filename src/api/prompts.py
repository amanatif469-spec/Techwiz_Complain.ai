from fastapi import APIRouter, HTTPException

from prompt_templates.loader import PROMPT_NAME, active_prompt_version, available_versions, list_prompt_files, read_template
from security.auth import StaffUser

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])


@router.get("")
def list_prompts(user: StaffUser):
    return {
        "active": {"name": PROMPT_NAME, "version": active_prompt_version()},
        "versions": available_versions(),
        "files": list_prompt_files(),
    }


@router.get("/{version}")
def get_prompt(version: str, user: StaffUser):
    if version not in available_versions():
        raise HTTPException(status_code=404, detail="Prompt version not found")
    return {"name": PROMPT_NAME, "version": version, "system": read_template(version, "system"), "user": read_template(version, "user")}
