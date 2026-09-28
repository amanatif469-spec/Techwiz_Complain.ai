import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from config.settings import ROOT_DIR, get_settings

PROMPT_DIR = ROOT_DIR / "prompt_templates"
# Prompts are plain text for an LLM, not HTML, so autoescaping stays off.
env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)), autoescape=False, undefined=StrictUndefined)
PROMPT_NAME = "complaint_intelligence"
_FILE = re.compile(rf"^{PROMPT_NAME}\.(v\d+)\.(system|user)\.j2$")


def available_versions() -> list[str]:
    versions = {m.group(1) for p in PROMPT_DIR.glob("*.j2") if (m := _FILE.match(p.name))}
    return sorted(versions, key=lambda v: int(v[1:]))


def active_prompt_version() -> str:
    """The configured PROMPT_VERSION, or the newest template on disk if it is missing."""
    configured = get_settings().prompt_version
    versions = available_versions()
    return configured if configured in versions else versions[-1]


PROMPT_VERSION = active_prompt_version()


def render_prompts(context: dict, version: str | None = None) -> tuple[str, str]:
    settings = get_settings()
    version = version or active_prompt_version()
    payload = {
        "organization_name": settings.organization_name,
        "organization_domain": settings.organization_domain,
        "tone": context.get("tone", "professional"),
        "prompt_version": version,
        **context,
    }
    system = env.get_template(f"{PROMPT_NAME}.{version}.system.j2").render(**payload)
    user = env.get_template(f"{PROMPT_NAME}.{version}.user.j2").render(**payload)
    return system, user


def read_template(version: str, kind: str) -> str:
    return (PROMPT_DIR / f"{PROMPT_NAME}.{version}.{kind}.j2").read_text(encoding="utf-8")


def list_prompt_files() -> list[str]:
    return sorted(p.name for p in Path(PROMPT_DIR).glob("*.j2"))
