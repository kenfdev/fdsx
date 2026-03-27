from typing import Literal

from pydantic import BaseModel, ConfigDict


class GeminiOptions(BaseModel):
    """Options for the Gemini CLI provider."""

    model_config = ConfigDict(extra="forbid")

    approval_mode: Literal["default", "auto_edit", "yolo", "plan"] | None = None
    yolo: bool = False
    sandbox: bool = False
    include_directories: list[str] = []
    extensions: list[str] = []
    policy: list[str] = []
    inactivity_timeout: int | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to Gemini CLI flags."""
        flags: list[str] = []
        if self.yolo:
            flags.append("--yolo")
        elif self.approval_mode is not None:
            flags.extend(["--approval-mode", self.approval_mode])
        if self.sandbox:
            flags.append("--sandbox")
        if self.include_directories:
            flags.extend(["--include-directories", ",".join(self.include_directories)])
        if self.extensions:
            flags.extend(["--extensions", ",".join(self.extensions)])
        for p in self.policy:
            flags.extend(["--policy", p])
        return flags
