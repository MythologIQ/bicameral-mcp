"""Interactive setup wizard for bicameral-mcp.

Guides the user through selecting a repo and installs the MCP server
config into Claude Code with local (project) scope.

Usage: bicameral-mcp setup
       bicameral-mcp setup /path/to/repo
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def _detect_repo(hint: str | None = None) -> Path:
    """Detect or prompt for the repo path."""
    if hint:
        p = Path(hint).resolve()
        if p.is_dir():
            return p
        print(f"  Path not found: {p}")

    cwd = Path.cwd()
    git_root = _find_git_root(cwd)

    if git_root:
        answer = input(f"\n  Detected git repo: {git_root}\n  Use this? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return git_root

    while True:
        raw = input("\n  Enter the path to your repo: ").strip()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return p
        print(f"  Not a directory: {p}")


def _find_git_root(start: Path) -> Path | None:
    """Walk up from start to find .git directory."""
    current = start
    for _ in range(20):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _detect_runner() -> tuple[str, list[str]]:
    """Detect the best available Python package runner."""
    if shutil.which("uvx"):
        return ("uvx", ["bicameral-mcp"])
    if shutil.which("pipx"):
        return ("pipx", ["run", "bicameral-mcp"])
    python = "python3" if shutil.which("python3") else "python"
    return (python, ["-m", "bicameral_mcp"])


def _build_config(repo_path: Path) -> dict:
    """Build the MCP server config object."""
    command, args = _detect_runner()
    # Prepare local data directory
    data_dir = repo_path / ".bicameral"
    data_dir.mkdir(parents=True, exist_ok=True)

    return {
        "command": command,
        "args": args,
        "env": {
            "REPO_PATH": str(repo_path),
            "SURREAL_URL": f"surrealkv://{data_dir / 'ledger.db'}",
            "CODE_LOCATOR_SQLITE_DB": str(data_dir / "code-graph.db"),
        },
    }


def _install_claude_code_local(repo_path: Path) -> bool:
    """Install via `claude mcp add-json` with local (project) scope.

    Creates .mcp.json in the repo root if it doesn't exist.
    """
    config = _build_config(repo_path)
    config_json = json.dumps(config)

    # First try via claude CLI
    if shutil.which("claude"):
        result = subprocess.run(
            ["claude", "mcp", "add-json", "bicameral", "--scope", "local", config_json],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(repo_path),
        )
        if result.returncode == 0:
            print(f"  Installed via Claude CLI (local scope) using {config['command']}")
            return True
        # If it failed because it already exists, that's fine
        if "already exists" in result.stderr:
            print(f"  Already configured in local scope — updating config")
            # Remove and re-add
            subprocess.run(
                ["claude", "mcp", "remove", "bicameral", "--scope", "local"],
                capture_output=True, text=True, timeout=10, cwd=str(repo_path),
            )
            result = subprocess.run(
                ["claude", "mcp", "add-json", "bicameral", "--scope", "local", config_json],
                capture_output=True, text=True, timeout=10, cwd=str(repo_path),
            )
            if result.returncode == 0:
                print(f"  Updated in Claude Code (local scope) using {config['command']}")
                return True

    # Fallback: write .mcp.json directly
    mcp_json_path = repo_path / ".mcp.json"
    existing = {}
    if mcp_json_path.exists():
        try:
            existing = json.loads(mcp_json_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing.setdefault("mcpServers", {})["bicameral"] = config
    mcp_json_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"  Wrote {mcp_json_path}")
    return True


def _install_skills(repo_path: Path) -> int:
    """Copy skill definitions into .claude/skills/ in the target repo."""
    # Skills are bundled in the package at skills/
    skills_src = Path(__file__).parent / "skills"
    if not skills_src.exists():
        return 0

    skills_dst = repo_path / ".claude" / "skills"
    installed = 0

    for skill_dir in sorted(skills_src.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        dst_dir = skills_dst / skill_dir.name
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_file = dst_dir / "SKILL.md"
        dst_file.write_text(skill_md.read_text())
        installed += 1

    return installed


def _ensure_gitignore(repo_path: Path) -> None:
    """Add .bicameral/ to .gitignore if not already there."""
    gitignore = repo_path / ".gitignore"
    entry = ".bicameral/"

    if gitignore.exists():
        content = gitignore.read_text()
        if entry in content:
            return
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# Bicameral MCP local data\n{entry}\n"
        gitignore.write_text(content)
    else:
        gitignore.write_text(f"# Bicameral MCP local data\n{entry}\n")

    print(f"  Added {entry} to .gitignore")


def run_setup(repo_hint: str | None = None) -> int:
    """Run the interactive setup wizard."""
    print()
    print("  ┌─────────────────────────────────────────┐")
    print("  │  Bicameral MCP — Setup                   │")
    print("  │  Decision ledger for your codebase        │")
    print("  └─────────────────────────────────────────┘")
    print()

    repo_path = _detect_repo(repo_hint)
    print(f"\n  Repo: {repo_path}")

    command, _ = _detect_runner()
    if command not in ("uvx", "pipx"):
        print(f"\n  Note: using '{command} -m bicameral_mcp' as runner.")
        print("  Install a package runner for zero-install: pip install pipx")

    # Ensure .bicameral/ is gitignored
    _ensure_gitignore(repo_path)

    # Install MCP server config
    print()
    _install_claude_code_local(repo_path)

    # Install slash command skills
    num_skills = _install_skills(repo_path)
    if num_skills:
        print(f"  Installed {num_skills} skills to .claude/skills/")

    print(f"\n  Done! Bicameral MCP is configured for: {repo_path}")
    print()
    print("  Slash commands available:")
    print("    /bicameral:ingest  — ingest a meeting transcript or PRD")
    print("    /bicameral:search  — pre-flight: check prior decisions before coding")
    print("    /bicameral:drift   — code review: check a file for drifted decisions")
    print("    /bicameral:status  — dashboard: implementation status of all decisions")
    print()

    return 0
