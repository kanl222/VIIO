"""Dynamic Skills Loader for VIIO Assistent.

Discovers and exposes procedural skills and runbooks from .agents/skills/
as LangChain tools for progressive disclosure during agent turns.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import yaml
from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Discovers and caches available agent skills from project directories."""

    def __init__(
        self,
        search_paths: list[Path | str] | Path | str | None = None,
        skills_dir: str | Path | None = None,
    ) -> None:
        service_root = Path(__file__).resolve().parents[4]
        repo_root = next(
            (parent for parent in service_root.parents if (parent / "services").is_dir()),
            service_root,
        )
        paths: list[Path] = []
        if skills_dir:
            sd = Path(skills_dir)
            paths.extend([repo_root / sd, service_root / sd, sd])
        if search_paths:
            if isinstance(search_paths, (str, Path)):
                sp_list = [search_paths]
            else:
                sp_list = list(search_paths)
            paths.extend(Path(p) for p in sp_list)
        else:
            paths.extend([
                repo_root / "skills",
                repo_root / ".agents" / "skills",
                repo_root / ".agent" / "skills",
                service_root / "skills",
                Path("skills"),
                Path(".agents/skills"),
            ])
        self.search_paths = paths
        self._skills: dict[str, dict[str, Any]] = {}
        self.refresh()

    def refresh(self) -> None:
        self._skills.clear()
        seen = set()
        for base in self.search_paths:
            if not base.exists():
                continue
            for skill_file in base.glob("*/SKILL.md"):
                resolved = skill_file.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    content = skill_file.read_text(encoding="utf-8")
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            meta = yaml.safe_load(parts[1]) or {}
                            instructions = parts[2].strip()
                            name = str(meta.get("name", skill_file.parent.name)).strip()
                            desc = str(meta.get("description", "")).strip()
                            self._skills.setdefault(name.lower(), {
                                "name": name,
                                "description": desc,
                                "instructions": instructions,
                                "path": skill_file,
                            })
                except Exception as e:
                    logger.debug("Failed to read skill at %s: %s", skill_file, e)

    def list_skills(self) -> list[dict[str, str]]:
        return [
            {"name": v["name"], "description": v["description"]}
            for v in self._skills.values()
        ]

    def get_skill(self, name: str) -> dict[str, Any] | None:
        clean = name.strip().lower()
        if clean in self._skills:
            return self._skills[clean]
        # Partial match
        for k, v in self._skills.items():
            if clean in k or k in clean:
                return v
        return None


def create_skills_tools(
    registry: SkillRegistry | None = None,
    skills_dir: str | Path | None = None,
) -> list[BaseTool]:
    """Create LangChain tools to discover and activate skills."""
    reg = registry or SkillRegistry(skills_dir=skills_dir)

    @tool("list_agent_skills")
    def list_agent_skills() -> str:
        """Показать список всех установленных экспертных скиллов (навыков и методик), доступных агенту."""
        skills = reg.list_skills()
        if not skills:
            return "Установленные специализированные скиллы не найдены."
        lines = ["Доступные экспертные скиллы:"]
        for s in skills:
            lines.append(f"- **{s['name']}**: {s['description']}")
        return "\n".join(lines)

    @tool("activate_agent_skill")
    def activate_agent_skill(skill_name: str) -> str:
        """Активировать специализированный скилл и получить детальные инструкции, стандарты и процедуры по его имени.

        skill_name: Имя скилла (например: 'docker-compose-patterns', 'google-agents-cli-eval', 'postgres-patterns', 'fastapi').
        """
        skill = reg.get_skill(skill_name)
        if not skill:
            avail = ", ".join(f"'{s['name']}'" for s in reg.list_skills())
            return f"Скилл '{skill_name}' не найден. Доступные скиллы: {avail}"
        return f"# Руководство по скиллу: {skill['name']}\n\n{skill['instructions']}"

    return [list_agent_skills, activate_agent_skill]
