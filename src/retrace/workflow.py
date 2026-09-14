"""Immutable workflow definitions and an explicitly versioned execution contract."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import Any

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_JSON_BYTES = 1_048_576


def encode(value: Any) -> str:
    """Validate the persistence boundary; never pickle user-controlled data."""
    data = json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)
    if len(data.encode()) > MAX_JSON_BYTES:
        raise ValueError("JSON payload exceeds 1 MiB; store large artifacts externally")
    return data


@dataclass(frozen=True)
class RetryPolicy:
    """Failed attempts consume the budget; interrupted attempts do not."""

    max_attempts: int = 3
    initial_delay: float = 0.25
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        for value in (self.initial_delay, self.max_delay):
            if not math.isfinite(value) or value < 0:
                raise ValueError("retry delays must be finite and nonnegative")
        if self.max_delay < self.initial_delay:
            raise ValueError("max_delay must be >= initial_delay")

    def delay(self, failures: int) -> float:
        # Bound the exponent before multiplying, even for extreme user budgets.
        return min(self.max_delay, self.initial_delay * 2.0 ** min(max(0, failures - 1), 60))


@dataclass(frozen=True)
class Context:
    run_id: str
    task_name: str
    attempt: int
    input: Any
    dependencies: Mapping[str, Any]

    @property
    def idempotency_key(self) -> str:
        """Stable across retries and process restarts for this logical step."""
        return hashlib.sha256(f"{self.run_id}:{self.task_name}".encode()).hexdigest()


@dataclass(frozen=True)
class Task:
    name: str
    fn: Callable[[Context], Awaitable[Any]]
    needs: tuple[str, ...] = ()
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"invalid task name: {self.name!r}")
        if not inspect.iscoroutinefunction(self.fn):
            raise TypeError(f"{self.name}: task function must be async")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        object.__setattr__(self, "needs", tuple(self.needs))
        if len(set(self.needs)) != len(self.needs):
            raise ValueError(f"{self.name}: duplicate dependencies")


@dataclass(frozen=True)
class Workflow:
    name: str
    tasks: tuple[Task, ...]
    version: str = "1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tasks", tuple(self.tasks))
        if not _NAME.fullmatch(self.name) or not self.version or len(self.version) > 128:
            raise ValueError("workflow needs a valid name and a version of 1–128 characters")
        if not self.tasks:
            raise ValueError("workflow must contain at least one task")
        names = {task.name for task in self.tasks}
        if len(names) != len(self.tasks):
            raise ValueError("task names must be unique")
        for task in self.tasks:
            missing = set(task.needs) - names
            if missing:
                raise ValueError(f"{task.name}: unknown dependencies {sorted(missing)}")
        try:
            tuple(TopologicalSorter({task.name: task.needs for task in self.tasks}).static_order())
        except CycleError as exc:
            raise ValueError("workflow contains a dependency cycle") from exc

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "tasks": [
                {
                    "name": t.name,
                    "needs": sorted(t.needs),
                    "function": f"{t.fn.__module__}:{t.fn.__qualname__}",
                    "timeout": t.timeout,
                    "max_attempts": t.retry.max_attempts,
                    "initial_delay": t.retry.initial_delay,
                    "max_delay": t.retry.max_delay,
                }
                for t in sorted(self.tasks, key=lambda task: task.name)
            ],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(encode(self.manifest).encode()).hexdigest()
