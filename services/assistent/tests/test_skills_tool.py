"""Тесты для механизма загрузки и вызова скиллов агента."""

from unittest.mock import patch
from assistent import create_app
from assistent.config import Settings
from assistent.infrastructure.tools.skills_tool import SkillRegistry, create_skills_tools


def test_skill_registry_discovers_skills():
    registry = SkillRegistry()
    skills = registry.list_skills()
    assert len(skills) > 0
    names = {s["name"].lower() for s in skills}
    assert "google-agents-cli-eval" in names or "fastapi" in names or "docker-compose-patterns" in names


def test_activate_agent_skill_returns_instructions():
    tools = create_skills_tools()
    tools_by_name = {t.name: t for t in tools}
    assert "list_agent_skills" in tools_by_name
    assert "activate_agent_skill" in tools_by_name

    activate = tools_by_name["activate_agent_skill"]
    content = activate.invoke({"skill_name": "google-agents-cli-eval"})
    assert "Руководство по скиллу" in content or "Agent Evaluation Guide" in content

    not_found = activate.invoke({"skill_name": "non_existent_skill_xyz"})
    assert "не найден" in not_found


def test_skills_enabled_registers_tools_in_app():
    settings = Settings(_env_file=None, skills_dir="skills")
    with patch("assistent.create_chat_model"), patch("assistent.build_agent_graph") as build:
        create_app(settings=settings)
    tools = {t.name for t in build.call_args.kwargs["tools"]}
    assert "list_agent_skills" in tools
    assert "activate_agent_skill" in tools


def test_skill_registry_prioritizes_skills_folder(tmp_path):
    skill_dir = tmp_path / "skills" / "custom-test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: custom-test-skill\ndescription: Custom skill for testing\n---\n\n# Custom Guide",
        encoding="utf-8",
    )
    reg = SkillRegistry(skills_dir=tmp_path / "skills")
    skill = reg.get_skill("custom-test-skill")
    assert skill is not None
    assert skill["name"] == "custom-test-skill"
    assert "Custom Guide" in skill["instructions"]


def test_registry_finds_project_skills_outside_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = SkillRegistry(skills_dir="skills")
    assert registry.get_skill("google-agents-cli-eval") is not None


def test_explicit_skills_take_priority_over_fallback(tmp_path):
    for folder, content in (("skills", "Preferred guide"), ("fallback", "Other guide")):
        skill = tmp_path / folder / "example" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: example\ndescription: Example\n---\n" + content, encoding="utf-8")
    registry = SkillRegistry(skills_dir=tmp_path / "skills", search_paths=[tmp_path / "fallback"])
    assert registry.get_skill("example")["instructions"] == "Preferred guide"
