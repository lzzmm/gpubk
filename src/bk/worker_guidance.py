from __future__ import annotations

import shlex
from dataclasses import dataclass


WORKER_STATUS_COMMAND = "bk w"
WORKER_FOREGROUND_COMMAND = "bk w start"
WORKER_INSTALL_COMMAND = "bk service install worker"
WORKER_ENABLE_COMMAND = "systemctl --user enable --now bk-worker.service"
WORKER_VERIFY_COMMAND = "bk doctor --require-worker --strict"


@dataclass(frozen=True)
class WorkerGuidance:
    username: str = "USER"

    @property
    def admin_persistence_argv(self) -> tuple[str, ...]:
        return ("sudo", "bk", "admin", "worker-persistence", "enable", self.username)

    @property
    def admin_persistence_command(self) -> str:
        return shlex.join(self.admin_persistence_argv)

    @property
    def user_setup_commands(self) -> tuple[str, str]:
        return WORKER_INSTALL_COMMAND, WORKER_ENABLE_COMMAND

    def as_dict(self) -> dict:
        return {
            "status_argv": shlex.split(WORKER_STATUS_COMMAND),
            "foreground_argv": shlex.split(WORKER_FOREGROUND_COMMAND),
            "user_setup_argv": [shlex.split(command) for command in self.user_setup_commands],
            "admin_persistence_argv": list(self.admin_persistence_argv),
            "temporary_transport": "tmux",
            "temporary_survives_logout": True,
            "temporary_survives_reboot": False,
        }
