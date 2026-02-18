"""Provider configuration — TOML parsing with .env fallback."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workrecap.config import AppConfig

KNOWN_TASKS = ("enrich", "daily", "weekly", "monthly", "yearly", "query")
VALID_STRATEGIES = ("economy", "standard", "premium", "adaptive", "fixed")


@dataclass
class TaskConfig:
    """Per-task LLM configuration."""

    provider: str
    model: str
    escalation_model: str | None = None


@dataclass
class ProviderEntry:
    """Per-provider credentials and settings."""

    api_key: str
    base_url: str | None = None


@dataclass
class ProviderConfig:
    """Manages provider/task configuration from TOML or .env fallback.

    Usage:
        # From TOML file
        pc = ProviderConfig(config_path=Path(".provider/config.toml"))

        # Fallback from AppConfig (no TOML)
        pc = ProviderConfig(config_path=None, fallback_config=app_config)
    """

    _strategy_mode: str = "fixed"
    _providers: dict[str, ProviderEntry] = field(default_factory=dict)
    _tasks: dict[str, TaskConfig] = field(default_factory=dict)

    def __init__(
        self,
        config_path: Path | None,
        fallback_config: AppConfig | None = None,
    ) -> None:
        if config_path is not None:
            if not config_path.exists():
                raise FileNotFoundError(f"Provider config not found: {config_path}")
            self._load_toml(config_path)
        elif fallback_config is not None:
            self._load_fallback(fallback_config)
        else:
            raise ValueError("Either config_path or fallback_config must be provided")

    def _load_toml(self, path: Path) -> None:
        with open(path, "rb") as f:
            data = tomllib.load(f)

        # Strategy
        strategy = data.get("strategy", {})
        self._strategy_mode = strategy.get("mode", "fixed")

        # Providers
        self._providers = {}
        for name, entry in data.get("providers", {}).items():
            self._providers[name] = ProviderEntry(
                api_key=entry.get("api_key", ""),
                base_url=entry.get("base_url"),
            )

        # Tasks
        self._tasks = {}
        for name, task in data.get("tasks", {}).items():
            self._tasks[name] = TaskConfig(
                provider=task["provider"],
                model=task["model"],
                escalation_model=task.get("escalation_model"),
            )

    def _load_fallback(self, config: AppConfig) -> None:
        self._strategy_mode = "fixed"
        self._providers = {
            config.llm_provider: ProviderEntry(api_key=config.llm_api_key),
        }
        default_task = TaskConfig(provider=config.llm_provider, model=config.llm_model)
        self._tasks = {"default": default_task}

    def get_task_config(self, task: str) -> TaskConfig:
        """Get configuration for a specific task, falling back to 'default'."""
        if task in self._tasks:
            return self._tasks[task]
        if "default" in self._tasks:
            return self._tasks["default"]
        raise KeyError(f"No config for task '{task}' and no default defined")

    def get_provider_entry(self, provider: str) -> ProviderEntry:
        """Get credentials for a specific provider."""
        if provider not in self._providers:
            raise KeyError(f"Provider '{provider}' not configured")
        return self._providers[provider]

    @property
    def strategy_mode(self) -> str:
        return self._strategy_mode

    @property
    def providers(self) -> dict[str, ProviderEntry]:
        return self._providers

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of error messages (empty = OK)."""
        errors: list[str] = []

        # Check strategy mode
        if self._strategy_mode not in VALID_STRATEGIES:
            errors.append(
                f"Invalid strategy mode '{self._strategy_mode}'. "
                f"Must be one of: {', '.join(VALID_STRATEGIES)}"
            )

        # Check that each task's provider is defined
        for task_name, task_config in self._tasks.items():
            if task_name == "default":
                continue
            if task_config.provider not in self._providers:
                errors.append(
                    f"Task '{task_name}' references provider '{task_config.provider}' "
                    f"which is not defined in [providers]"
                )

        # Check api_key presence (custom providers with empty key are OK)
        for name, entry in self._providers.items():
            if name == "custom":
                continue
            if not entry.api_key:
                errors.append(f"Provider '{name}' has empty api_key")

        return errors
