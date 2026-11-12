"""A scripted, model-free Harbor agent for the Signal experiments.

It runs a fixed debugging playbook (agents/playbooks/<name>.json) inside the task
container through Harbor's own environment.exec, and records what actually happened:

    <logs_dir>/signal_exec_log.jsonl   one line per command: ids, start/end times,
                                       exit code, stdout/stderr (bounded), duration
    <logs_dir>/trajectory.json         the same run as ATIF, validated against Harbor's
                                       own Trajectory model before it is written

It is NOT a language-model agent. No model is called, nothing is paid for, and no
token counts exist: the trajectory carries no metrics and says so in `notes`. It is
used to exercise the Harbor -> Signal -> COPS path with real command executions
before a live coding agent is approved.

Run (from experiments/harbor):
    PYTHONPATH=agents harbor run -p tasks/h01-normal-debugging \
        --agent-import-path scripted_debug_agent:ScriptedDebugAgent --ak playbook=h01
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Agent, Observation, ObservationResult, Step, ToolCall, Trajectory

PLAYBOOKS = Path(__file__).parent / "playbooks"
MAX_OUTPUT = 8000                     # characters kept per stream per command
AGENT_NAME = "signal-scripted-debug"
AGENT_VERSION = "1.0.0"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ScriptedDebugAgent(BaseAgent):

    def __init__(self, logs_dir, model_name=None, playbook=None, step_timeout_sec=120, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=None, **kwargs)
        if not playbook:
            raise ValueError("pass --ak playbook=<name> (a file in agents/playbooks)")
        self._playbook_name = playbook
        self._playbook = json.loads((PLAYBOOKS / f"{playbook}.json").read_text())
        self._step_timeout = int(step_timeout_sec)

    @staticmethod
    def name():
        return AGENT_NAME

    def version(self):
        return AGENT_VERSION

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        logs = Path(self.logs_dir)
        logs.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid.uuid4())
        steps = [Step(step_id=1, timestamp=_now(), source="user", message=instruction)]
        records = []
        for i, st in enumerate(self._playbook["steps"], start=1):
            call_id = f"call-{i:03d}"
            started, t0 = _now(), time.monotonic()
            try:
                res = await environment.exec(command=st["command"], timeout_sec=self._step_timeout)
                rc, out, err, error = res.return_code, res.stdout or "", res.stderr or "", None
            except Exception as e:                     # a command that could not run is recorded, not hidden
                rc, out, err, error = None, "", "", f"{type(e).__name__}: {e}"[:500]
            ended, ms = _now(), round((time.monotonic() - t0) * 1000)
            rec = {"run_id": run_id, "seq": i, "call_id": call_id, "tool": "bash", "command": st["command"],
                   "say": st.get("say"), "intent": st.get("intent"), "edit": st.get("edit"),
                   "started_at": started, "ended_at": ended, "duration_ms": ms, "exit_code": rc,
                   "stdout": out[-MAX_OUTPUT:], "stderr": err[-MAX_OUTPUT:],
                   "stdout_truncated": len(out) > MAX_OUTPUT, "stderr_truncated": len(err) > MAX_OUTPUT,
                   "exec_error": error}
            records.append(rec)
            content = (out + (("\n[stderr]\n" + err) if err else ""))[-MAX_OUTPUT:] if error is None else error
            steps.append(Step(
                step_id=len(steps) + 1, timestamp=started, source="agent", message=st.get("say") or "",
                tool_calls=[ToolCall(tool_call_id=call_id, function_name="bash", arguments={"command": st["command"]})],
                observation=Observation(results=[ObservationResult(
                    source_call_id=call_id, content=content,
                    extra={"exit_code": rc, "duration_ms": ms, "ended_at": ended, "exec_error": error})]),
                extra={"playbook_step": i, "intent": st.get("intent"), "edit": st.get("edit")}))
        with open(logs / "signal_exec_log.jsonl", "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        traj = Trajectory(
            session_id=run_id,
            agent=Agent(name=AGENT_NAME, version=AGENT_VERSION, model_name=None,
                        extra={"scripted": True, "playbook": self._playbook_name,
                               "playbook_task": self._playbook.get("task")}),
            steps=steps,
            notes=("Scripted, model-free agent: the steps are a fixed playbook, not model decisions. "
                   "No model was called and no token usage exists."))
        # validate against Harbor's own ATIF model before writing
        Trajectory.model_validate(json.loads(traj.model_dump_json()))
        (logs / "trajectory.json").write_text(traj.model_dump_json(indent=1, exclude_none=True))
        context.metadata = {"scripted": True, "playbook": self._playbook_name, "commands": len(records),
                            "failed_commands": sum(1 for r in records if r["exit_code"] not in (0,)),
                            "run_id": run_id}
