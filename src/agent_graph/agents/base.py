import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from agent_graph.logging_config import debug, step

if TYPE_CHECKING:
    from agent_graph.state import TaskState

logger = logging.getLogger(__name__)


class AgentProtocol(ABC):
    """Contract for all agents in the graph."""

    @abstractmethod
    def run(self, state: "TaskState") -> dict:
        """Execute the agent's logic and return state updates."""
        ...


class BaseAgent(AgentProtocol):
    """Abstract base providing shared agent behavior."""

    name: str = "base"
    prompt_template: str = ""

    @abstractmethod
    def _execute(self, state: "TaskState") -> dict:
        """Subclasses implement actual logic here."""
        ...

    def run(self, state: "TaskState") -> dict:
        """Wraps _execute with logging and error handling."""
        try:
            step(f"[{self.name}] starting")
            debug(f"[{self.name}] starting")
            result = self._execute(state)
            step(f"[{self.name}] completed")
            debug(f"[{self.name}] completed")
            return result
        except Exception as e:
            msg = f"[{self.__class__.__name__}] Error: {e}"
            logger.error(msg)
            print(msg)
            raise RuntimeError(msg) from e
