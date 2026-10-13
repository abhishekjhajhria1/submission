"""Settings (.env) and the company profile (profiles/<name>/profile.toml + policy.py)."""
from __future__ import annotations

import importlib.util
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    provider: str = field(default_factory=lambda: os.getenv("FARMHAND_PROVIDER", "gemini"))  # gemini | openai
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", ""))
    model: str = field(default_factory=lambda: os.getenv("FARMHAND_MODEL", "gemini-3.8-flash"))
    # Free-tier Gemini allows ~10 requests/minute; space calls out instead of hitting 429s.
    min_seconds_between_calls: float = field(default_factory=lambda: float(os.getenv("LLM_MIN_INTERVAL", "6")))
    profile: str = field(default_factory=lambda: os.getenv("FARMHAND_PROFILE", "patil_farms"))
    runs_dir: Path = ROOT / "runs"


@dataclass
class Profile:
    data: dict
    folder: Path
    requires_approval: Callable[[str, dict, dict], str | None]

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def today(self) -> date:
        return date.fromisoformat(self.data["today"]) if self.data.get("today") else date.today()

    def calendar(self, days: int = 8) -> str:
        """'Tue 2026-10-13 (today), Wed 2026-10-14 (tomorrow), ...' - models are unreliable at weekday arithmetic."""
        labels = {0: " (today)", 1: " (tomorrow)"}
        return ", ".join(f"{(self.today + timedelta(d)):%a %Y-%m-%d}{labels.get(d, '')}" for d in range(days))

    def prompt_vars(self) -> dict:
        return {"business": self.name, "profile": self.describe(), "today": self.today.isoformat(),
                "weekday": self.today.strftime("%A"), "calendar": self.calendar()}

    @property
    def files_root(self) -> Path:
        return (ROOT / self.data["files"]["root"]).resolve()

    def limit(self, key: str, default: int) -> int:
        return int(self.data.get("limits", {}).get(key, default))

    def describe(self) -> str:
        """The profile as prompt text: who the agent works for and the house rules."""
        d = self.data
        rules = "\n".join(f"- {r}" for r in d.get("sop", {}).get("rules", []))
        return (f"Business: {d['name']} (owner: {d['owner']})\n{d['about'].strip()}\n"
                f"Language: {d.get('language', '').strip()}\n\nStanding rules (SOP):\n{rules}")


def load_profile(name: str) -> Profile:
    folder = ROOT / "profiles" / name
    with open(folder / "profile.toml", "rb") as f:
        data = tomllib.load(f)
    hook: Callable[[str, dict, dict], str | None] = lambda tool, args, profile: None
    policy_file = folder / "policy.py"
    if policy_file.exists():
        spec = importlib.util.spec_from_file_location(f"profile_policy_{name}", policy_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        hook = module.requires_approval
    return Profile(data, folder, hook)
