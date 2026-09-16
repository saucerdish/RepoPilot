from pathlib import Path

from .base import Tool


class SkillLoader(Tool):
    name = "load_skill"
    description = "Load full repository skill instructions by catalog name."

    def __init__(self, workspace: Path):
        self.root = workspace.resolve() / "skills"
        self.skills = {}
        for path in sorted(self.root.glob("*/SKILL.md")):
            if not path.resolve().is_relative_to(self.root.resolve()):
                continue
            text = path.read_text(encoding="utf-8")
            metadata = {}
            if text.startswith("---\n"):
                front, separator, _ = text[4:].partition("\n---")
                if separator:
                    for line in front.splitlines():
                        key, sep, value = line.partition(":")
                        if sep:
                            metadata[key.strip()] = value.strip().strip("\"'")
            name = metadata.get("name", path.parent.name)
            if name in self.skills:
                raise ValueError(f"Duplicate skill name: {name}")
            self.skills[name] = (metadata.get("description", "Repository skill"), text)

    def catalog(self):
        return "\n".join(f"- {name}: {description}" for name, (description, _) in self.skills.items()) or "(none)"

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({"properties": {"name": {"type": "string"}}, "required": ["name"]})
        return schema

    def run(self, name: str):
        return self.skills[name][1] if name in self.skills else f"Error: unknown skill {name}"
