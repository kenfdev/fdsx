from pathlib import Path


def needs_init(cwd: Path) -> bool:
    return not (cwd / ".fdsx").is_dir()
