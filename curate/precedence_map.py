#!/usr/bin/env python3
"""
Project Signal — field precedence map

For every field in the observables table, this says which collector supplies
it, in preference order, per tool. The curation layer reads this to decide
who wins when several collectors report the same thing.

Order matters: first entry that has a value wins. Later entries are still
recorded in `contributing`, and a mismatch sets collectors_agree = false.

Verified against real captures on Claude Code 2.1.220 and Cursor 3.13.25,
July and August 2026. Codex, Copilot and Antigravity added September 2026,
each entry tagged with its basis; see EXTENDED below. Entries marked UNVERIFIED are reasoned from the field
existing, not from having seen it arrive.

    python3 precedence_map.py              # readable table
    python3 precedence_map.py --json       # machine readable
    python3 precedence_map.py --gaps       # fields no collector supplies
    python3 precedence_map.py --evidence codex   # basis for each entry
"""

import json
import sys

H, O, W, P, F, K, D = ("local_hooks", "otel", "cli_wrapper",
                       "mcp_proxy", "fs_watcher", "webhook", "derived")

# field: {tool: [collectors in preference order]}, note
# "" means no collector on that tool can supply it
PRECEDENCE = {

  # --- correlation ---------------------------------------------------------
  "session_id":        ({"claude-code": [H, O, F], "cursor": [H, F]},
                        "CC session_id, Cursor conversation_id. Verified equal across hooks and OTel."),
  "conversation_id":   ({"claude-code": [H], "cursor": [H]},
                        "CC reuses session_id. Cursor sends both, same value."),
  "turn_id":           ({"claude-code": [H, O], "cursor": [H]},
                        "CC prompt_id, verified identical to OTel prompt.id. Cursor generation_id."),
  "message_id":        ({"claude-code": [H], "cursor": []},
                        "MessageDisplay.message_id. No Cursor equivalent."),
  "tool_use_id":       ({"claude-code": [H, O], "cursor": [H]},
                        "Joins a tool request to its result."),
  "sequence_num":      ({"claude-code": [O], "cursor": [D]},
                        "OTel event.sequence. Cursor has none, order by timestamp."),

  # --- agent identity ------------------------------------------------------
  "agent_id":          ({"claude-code": [H, O, F], "cursor": []},
                        "CC on most payloads. Cursor has no agent identity at all."),
  "agent_type":        ({"claude-code": [H], "cursor": []},
                        "SubagentStart.agent_type, observed value 'fork'."),
  "parent_agent_id":   ({"claude-code": [O, D], "cursor": []},
                        "OTel beta traces. Otherwise derive from nesting."),
  "is_multi_agent":    ({"claude-code": [D], "cursor": [D]},
                        "Derived by signal_agents.py from four independent clues."),
  "delegation_depth":  ({"claude-code": [D], "cursor": [D]}, "Derived from agent_id nesting."),

  # --- operator and org ----------------------------------------------------
  "operator_email":    ({"claude-code": [O], "cursor": [H]},
                        "MIRROR IMAGE. CC only in OTel, Cursor on every hook payload."),
  "operator_id":       ({"claude-code": [O], "cursor": []}, "OTel user.account_id."),
  "org_id":            ({"claude-code": [O], "cursor": []},
                        "OTel organization.id. Cursor needs the admin API."),
  "operator_username": ({"claude-code": [W, F], "cursor": [W, F]}, "OS username from the collector."),
  "hostname":          ({"claude-code": [W], "cursor": [W]}, "Collector environment."),
  "team_id":           ({"claude-code": [O], "cursor": []},
                        "Only because we inject it via OTEL_RESOURCE_ATTRIBUTES."),

  # --- where ---------------------------------------------------------------
  "workspace":         ({"claude-code": [H], "cursor": [H]},
                        "CC cwd, Cursor workspace_roots[0]."),
  "cwd":               ({"claude-code": [H, W], "cursor": [H]}, ""),
  "git_branch":        ({"claude-code": [F, W], "cursor": [F, W]},
                        "Neither tool reports it. Read from .git or a git command."),
  "git_commit":        ({"claude-code": [F, K], "cursor": [F, K]}, "Watcher or webhook."),

  # --- tool version --------------------------------------------------------
  "tool_version":      ({"claude-code": [O], "cursor": [H]},
                        "MIRROR AGAIN. CC service.version in OTel, Cursor cursor_version on hooks."),

  # --- prompt --------------------------------------------------------------
  "prompt_text":       ({"claude-code": [H, F], "cursor": [H, F]},
                        "Hooks always. OTel redacts it by default, verified as <REDACTED>."),
  "prompt_length":     ({"claude-code": [O, D], "cursor": [D]}, "OTel gives it even when redacted."),
  "prompt_word_count": ({"claude-code": [D], "cursor": [D]}, "From the text."),
  "attachment_count":  ({"claude-code": [], "cursor": [H]}, "Cursor only."),
  "composer_mode":     ({"claude-code": [], "cursor": [H]}, "Cursor only: agent, ask or edit."),
  "slash_command":     ({"claude-code": [O, D], "cursor": [D]}, "OTel command_name, verified."),
  "permission_mode":   ({"claude-code": [H], "cursor": []}, "On most CC payloads."),
  "effort_level":      ({"claude-code": [H], "cursor": []}, "effort.level, an object not a string."),

  # --- response ------------------------------------------------------------
  "response_text":     ({"claude-code": [H, O, F], "cursor": [H, F]},
                        "CC MessageDisplay.delta in parts, or Stop.last_assistant_message whole."),
  "response_index":    ({"claude-code": [H], "cursor": []}, "CC streams responses, Cursor does not."),
  "response_is_final": ({"claude-code": [H], "cursor": []}, ""),
  "reasoning_text":    ({"claude-code": [F], "cursor": [H]},
                        "CC thinking blocks live in the transcript. Cursor emits afterAgentThought."),

  # --- model call ----------------------------------------------------------
  "model":             ({"claude-code": [H, O, F], "cursor": [H]}, "CC SessionStart.model."),
  "model_id":          ({"claude-code": [O], "cursor": [H]}, ""),
  "input_tokens":      ({"claude-code": [F, O], "cursor": [H]},
                        "THE KEY ASYMMETRY. CC in the transcript, Cursor in the hook payload."),
  "output_tokens":     ({"claude-code": [F, O], "cursor": [H]}, "Same."),
  "cache_read_tokens": ({"claude-code": [F, O], "cursor": [H]}, "Same."),
  "cache_write_tokens":({"claude-code": [F, O], "cursor": [H]}, "Same."),
  "total_tokens":      ({"claude-code": [F, O, D], "cursor": [H, D]}, "Sum if not given."),
  "cost_usd":          ({"claude-code": [O], "cursor": [D]},
                        "CC emits claude_code.cost.usage. Cursor has no cost field, must estimate."),
  "request_latency_ms":({"claude-code": [O], "cursor": []}, "OTel api_request.duration_ms."),
  "time_to_first_token":({"claude-code": [O], "cursor": []}, "OTel only."),
  "stop_reason":       ({"claude-code": [O], "cursor": [H]}, "Cursor stop.status."),
  "thinking_blocks":   ({"claude-code": [F], "cursor": [D]}, "Counted from the transcript."),
  "sampling_params":   ({"claude-code": [], "cursor": [H]},
                        "Cursor model_params on afterAgentThought. CC does not expose it."),

  # --- tool calls ----------------------------------------------------------
  "tool_name":         ({"claude-code": [H, O], "cursor": [H]}, ""),
  "tool_arguments":    ({"claude-code": [H, P], "cursor": [H, P]},
                        "Hooks give the full input. Proxy is the fallback for MCP."),
  "tool_result":       ({"claude-code": [H, P], "cursor": [H, P]},
                        "CC tool_response object. Cursor tool_output, a JSON string."),
  "tool_duration_ms":  ({"claude-code": [H], "cursor": [H]},
                        "CC duration_ms on PostToolUse, Cursor duration. Both verified."),
  "tool_interrupted":  ({"claude-code": [H], "cursor": []}, "tool_response.interrupted."),
  "tool_timeout_ms":   ({"claude-code": [], "cursor": [H]}, "Cursor tool_input.timeout."),
  "tool_result_size":  ({"claude-code": [O, D], "cursor": [D]}, ""),
  "tool_is_builtin":   ({"claude-code": [D], "cursor": [D]}, "Tool name against the built-in set."),
  "batch_size":        ({"claude-code": [H], "cursor": []}, "PostToolBatch.tool_calls. CC only."),

  # --- mcp -----------------------------------------------------------------
  "mcp_server":        ({"claude-code": [P, H, O], "cursor": [P, H]}, ""),
  "mcp_tool":          ({"claude-code": [P, H], "cursor": [P, H]}, ""),
  "mcp_tools_available":({"claude-code": [P], "cursor": [P]},
                        "Proxy only. The full menu the agent could reach, not just what it used."),
  "mcp_server_version":({"claude-code": [P], "cursor": [P]}, "From the initialize handshake."),
  "mcp_transport":     ({"claude-code": [P], "cursor": [P]}, "Known at deploy time really."),

  # --- shell ---------------------------------------------------------------
  "command":           ({"claude-code": [H, W], "cursor": [H, W]}, ""),
  "exit_code":         ({"claude-code": [W, D], "cursor": [H]},
                        "Cursor puts exitCode inside tool_output. CC needs the wrapper or stderr."),
  "stdout":            ({"claude-code": [H, W], "cursor": [H, W]},
                        "CC tool_response.stdout. Cursor afterShellExecution.output."),
  "stderr":            ({"claude-code": [H, W], "cursor": [W]},
                        "CC separates them. Cursor merges into output."),
  "sandboxed":         ({"claude-code": [], "cursor": [H]},
                        "Cursor only, and it explains why no permission events fire."),
  "hung_seconds":      ({"claude-code": [W], "cursor": [W]},
                        "WRAPPER ONLY. Nothing else can see silence while a process is alive."),
  "is_developer_command":({"claude-code": [W], "cursor": [W]},
                        "WRAPPER ONLY. Commands the human ran, which the agent never saw."),
  "risk_markers":      ({"claude-code": [D], "cursor": [D]}, "Pattern match on the command."),

  # --- files ---------------------------------------------------------------
  "file_path":         ({"claude-code": [H, F], "cursor": [H, F]},
                        "Cursor routes file work through Shell, so often inside the command."),
  "file_change_kind":  ({"claude-code": [F, H], "cursor": [F, H]}, "Watcher is ground truth."),
  "old_content":       ({"claude-code": [F], "cursor": [H, F]},
                        "Cursor afterFileEdit gives it directly, but only on the native edit path."),
  "new_content":       ({"claude-code": [F], "cursor": [H, F]}, "Same."),
  "chars_added":       ({"claude-code": [D], "cursor": [H, D]}, ""),
  "chars_removed":     ({"claude-code": [D], "cursor": [H, D]}, ""),
  "lines_added":       ({"claude-code": [O, D], "cursor": [D]},
                        "OTel claude_code.lines_of_code.count, verified."),
  "lines_removed":     ({"claude-code": [O, D], "cursor": [D]}, "Same."),
  "content_hash":      ({"claude-code": [F], "cursor": [F]}, "Watcher only."),
  "reverted_to_earlier":({"claude-code": [F], "cursor": [F]},
                        "WATCHER ONLY. Hash returning to an earlier state is an undo."),
  "file_size":         ({"claude-code": [F], "cursor": [F]}, ""),
  "is_binary":         ({"claude-code": [F], "cursor": [F]}, ""),
  "outside_workspace": ({"claude-code": [D], "cursor": [D]}, "Path against workspace root."),
  "is_config_file":    ({"claude-code": [F, D], "cursor": [F, D]}, ""),
  "is_dependency_manifest":({"claude-code": [F], "cursor": [F]}, ""),
  "is_lockfile":       ({"claude-code": [F], "cursor": [F]}, ""),

  # --- permission ----------------------------------------------------------
  "permission_decision":({"claude-code": [O, H, D], "cursor": []},
                        "OTel tool_decision. Hooks fire the request but never the denial. "
                        "Cursor fires nothing at all in sandbox mode."),
  "permission_source": ({"claude-code": [O, H], "cursor": []},
                        "Verified value user_temporary, meaning approved once not always."),
  "permission_scope":  ({"claude-code": [H, O], "cursor": []}, ""),
  "permission_options":({"claude-code": [H], "cursor": []},
                        "permission_suggestions. Shows what escalation was offered."),
  "denial_reason":     ({"claude-code": [], "cursor": []},
                        "NOT OBTAINABLE on either tool. The most valuable oversight signal."),

  # --- agent behaviour -----------------------------------------------------
  "turn_count":        ({"claude-code": [D], "cursor": [D]}, "Distinct turn_id per session."),
  "loop_count":        ({"claude-code": [D], "cursor": [H]}, "Cursor stop.loop_count."),
  "plan_item":         ({"claude-code": [], "cursor": []},
                        "TaskCreated and TaskCompleted registered but never fire."),
  "compaction_reason": ({"claude-code": [H], "cursor": [H]}, "Neither fired in testing."),
  "background_tasks":  ({"claude-code": [H], "cursor": []}, "Stop.background_tasks."),
  "scheduled_jobs":    ({"claude-code": [H], "cursor": []}, "Stop.session_crons."),
  "instructions_loaded":({"claude-code": [H, F], "cursor": [F]},
                        "InstructionsLoaded never fired. Watch CLAUDE.md instead."),

  # --- risk ----------------------------------------------------------------
  "sensitivity_tier":  ({"claude-code": [D], "cursor": [D]}, "Computed at capture from paths and args."),
  "violation_count":   ({"claude-code": [D], "cursor": [D]}, "Classifier."),
  "escalation_flag":   ({"claude-code": [D, H], "cursor": [D]}, ""),
  "secret_detected":   ({"claude-code": [D], "cursor": [D]},
                        "Shape match on values, not the word 'token' in a sentence."),
  "injection_markers": ({"claude-code": [D], "cursor": [D]}, "Over tool results from the proxy."),

  # --- artifacts and transcripts -------------------------------------------
  "raw_ref":           ({"claude-code": [H, F], "cursor": [H, F]},
                        "transcript_path is on every payload for both tools."),
}


COLLECTOR_NAMES = {
    H: "local hooks", O: "OpenTelemetry", W: "CLI wrapper",
    P: "MCP proxy", F: "FS watcher", K: "webhook", D: "derived",
}



# ==========================================================================
# Codex, Copilot and Antigravity
#
# Added September 2026. Built from the test_runs capture bundle (fifty
# sessions, ten prompts across five tools, second operator) and from our
# own Codex and Copilot runs. Kept separate from the entries above so the
# Claude Code and Cursor mapping is untouched and this can be reviewed as
# one change.
#
# Each entry carries its basis:
#   seen     the field arrived in captured payloads from that collector
#   raw      present in the raw payload, our extractor does not read it yet
#   absent   the condition arose across several sessions, nothing arrived
#   contract stated in the vendor's own hook documentation
#   reasoned inferred from the payload shape, not yet seen to arrive
# ==========================================================================

EXTENDED = {
  # field:        {tool: ([collectors], basis, note)}
  "session_id": {
    "codex":       ([H, O, F], "seen", "hooks session_id, OTel conversation.id, rollout session_meta"),
    "copilot":     ([H, O, W], "seen", "sessionId on every hook post"),
    "antigravity": ([H, W],    "seen", "conversationId in hooks, conversation_id in stream-json"),
  },
  "conversation_id": {
    "codex":       ([H, O], "seen",     "OTel names it conversation.id, not session.id"),
    "copilot":     ([H],    "reasoned", "reuses sessionId"),
    "antigravity": ([H, W], "seen",     "same value as session_id"),
  },
  "turn_id": {
    "codex":       ([H],    "reasoned", "hook payloads follow Claude Code's shape"),
    "copilot":     ([O, H], "reasoned", "traceparent on hook posts can join to OTel spans"),
    "antigravity": ([H],    "seen",     "stepIdx; a step, not a user turn"),
  },
  "tool_use_id": {
    "codex":       ([H], "reasoned", "pairs PreToolUse with PostToolUse"),
    "copilot":     ([O], "reasoned", "span id"),
    "antigravity": ([H], "reasoned", "toolCall.id where present"),
  },
  "sequence_num": {
    "codex": ([D], "reasoned", "order by timestamp"),
    "copilot": ([D], "reasoned", "order by timestamp"),
    "antigravity": ([H, D], "seen", "stepIdx gives a native order"),
  },
  "agent_id": {
    "codex":       ([H],  "seen",     "on hook payloads, including SubagentStart"),
    "copilot":     ([H],  "seen",     "agentId on hook posts; thin in our captures"),
    "antigravity": ([],   "reasoned", "no agent identity field observed"),
  },
  "agent_type": {
    "codex":       ([H], "seen",     "SubagentStart fires"),
    "copilot":     ([H], "seen",     "agentName and agentType on hook posts"),
    "antigravity": ([],  "reasoned", "none observed"),
  },
  "parent_agent_id": {
    "codex": ([D], "reasoned", "derive from subagent nesting"),
    "copilot": ([], "reasoned", ""), "antigravity": ([], "reasoned", ""),
  },
  "operator_email": {
    "codex": ([], "absent", "OTel carries auth mode, not identity"),
    "copilot": ([], "reasoned", "likely only on an enterprise tenant"),
    "antigravity": ([], "reasoned", ""),
  },
  "workspace": {
    "codex":       ([H, F], "seen", "cwd in hooks and rollout session_meta"),
    "copilot":     ([H],    "seen", "cwd on hook posts"),
    "antigravity": ([H, W], "seen", "workspacePaths; init cwd in stream-json"),
  },
  "cwd": {
    "codex": ([H, W, F], "seen", ""), "copilot": ([H, W], "seen", ""),
    "antigravity": ([W, H], "seen", ""),
  },
  "tool_version": {
    "codex":       ([O, F], "seen",     "OTel service.version; rollout cli_version"),
    "copilot":     ([O],    "reasoned", "service.version on github-copilot"),
    "antigravity": ([],     "absent",   "not in any payload; only on the binary"),
  },
  "prompt_text": {
    "codex":       ([H, F], "seen",     "UserPromptSubmit"),
    "copilot":     ([H, O], "seen",     "prompt on submission posts, which carry no hookName"),
    "antigravity": ([W],    "reasoned", "hooks carry no prompt; only the terminal sees it"),
  },
  "prompt_length": {
    "codex": ([D], "reasoned", ""), "copilot": ([D], "absent", "derive from text"),
    "antigravity": ([D], "reasoned", ""),
  },
  "permission_mode": {
    "codex": ([H], "reasoned", ""), "copilot": ([], "reasoned", ""),
    "antigravity": ([], "contract", "the capture ran with permissions skipped; untestable"),
  },
  "response_text": {
    "codex":       ([F, H], "raw",      "in the rollout; extractor does not read it yet"),
    "copilot":     ([O, F], "reasoned", ""),
    "antigravity": ([W, F], "reasoned", "stream-json step updates"),
  },
  "reasoning_text": {
    "codex":       ([F],    "raw",  "reasoning tokens counted; content not yet extracted"),
    "copilot":     ([O],    "raw",  "present in OTel; extractor does not read it yet"),
    "antigravity": ([W],    "seen", "stream-json step_update text"),
  },
  "model": {
    "codex":       ([F, O], "seen", "rollout turn_context.model"),
    "copilot":     ([O],    "raw",  "present in OTel spans"),
    "antigravity": ([H, W], "seen", "modelName; stream-json init"),
  },
  "model_id": {
    "codex": ([F], "seen", ""), "copilot": ([O], "raw", ""), "antigravity": ([H], "seen", ""),
  },
  "input_tokens": {
    "codex":       ([F], "seen", "rollout event_msg token_count, last_token_usage"),
    "copilot":     ([O], "raw",  "present in OTel; extractor does not read it yet"),
    "antigravity": ([W], "seen", "stream-json result.usage"),
  },
  "output_tokens": {
    "codex": ([F], "seen", ""), "copilot": ([O], "raw", ""), "antigravity": ([W], "seen", ""),
  },
  "cache_read_tokens": {
    "codex":       ([F], "seen", "cached_input_tokens"),
    "copilot":     ([O], "raw",  ""),
    "antigravity": ([W], "seen", "result.usage.cache_read_tokens"),
  },
  "cache_write_tokens": {
    "codex": ([F], "seen", "cache_write_input_tokens"),
    "copilot": ([O], "reasoned", ""), "antigravity": ([], "reasoned", ""),
  },
  "total_tokens": {
    "codex":       ([F, D], "seen",   ""),
    "copilot":     ([O, D], "absent", "total not sent; derive from input and output"),
    "antigravity": ([W, D], "seen",   "result.usage.total_tokens"),
  },
  "cost_usd": {
    "codex":       ([D],    "absent",   "no cost emitted; derive from tokens"),
    "copilot":     ([O, D], "raw",      "present in OTel"),
    "antigravity": ([D],    "reasoned", "derive from tokens"),
  },
  "request_latency_ms": {
    "codex":       ([O], "seen",     "codex.api_request duration_ms, sent as a string"),
    "copilot":     ([O], "reasoned", "span duration"),
    "antigravity": ([],  "absent",   "OTel emits nothing on this tool"),
  },
  "time_to_first_token": {
    "codex": ([], "reasoned", ""), "copilot": ([O], "reasoned", ""),
    "antigravity": ([], "absent", ""),
  },
  "stop_reason": {
    "codex":       ([], "absent", "across 26 sessions, nothing arrived"),
    "copilot":     ([], "absent", "across 25 sessions, nothing arrived"),
    "antigravity": ([H, W], "seen", "Stop.terminationReason; result.status"),
  },
  "thinking_blocks": {
    "codex":       ([F], "seen", "reasoning_output_tokens above zero"),
    "copilot":     ([O], "raw",  ""),
    "antigravity": ([W], "seen", "result.usage.thinking_tokens"),
  },
  "tool_name": {
    "codex":       ([H, O], "seen", ""),
    "copilot":     ([H, O], "seen", "toolName on permission requests"),
    "antigravity": ([H],    "seen", "toolCall.name, lowercased step type"),
  },
  "tool_arguments": {
    "codex":       ([H, P], "seen", ""),
    "copilot":     ([H, O], "seen", "toolInput"),
    "antigravity": ([H, P], "seen", "toolCall.args"),
  },
  "tool_result": {
    "codex":       ([H, P], "reasoned", "too few sessions to call it absent"),
    "copilot":     ([O, P], "reasoned", "too few sessions to call it absent"),
    "antigravity": ([P],    "contract", "PostToolUse carries stepIdx and error only"),
  },
  "tool_duration_ms": {
    "codex": ([O], "reasoned", ""), "copilot": ([O], "reasoned", ""),
    "antigravity": ([], "contract", "not in the hook payload"),
  },
  "mcp_server": {
    "codex": ([P, H], "seen", ""), "copilot": ([P], "reasoned", ""),
    "antigravity": ([P], "seen", "handshake only; tool calls do not route through MCP"),
  },
  "mcp_tool": {
    "codex": ([P, H], "seen", ""), "copilot": ([P], "reasoned", ""),
    "antigravity": ([P], "seen", "handshake only"),
  },
  "command": {
    "codex": ([H, W], "seen", ""), "copilot": ([H, W], "seen", ""),
    "antigravity": ([H], "seen", "toolCall.args.CommandLine"),
  },
  "exit_code": {
    "codex":       ([W, H], "reasoned", ""),
    "copilot":     ([W],    "seen",     ""),
    "antigravity": ([H, D], "contract", "PostToolUse error reads 'exit status 1'"),
  },
  "stdout": {
    "codex": ([H, W], "reasoned", ""), "copilot": ([W], "absent", "terminal only"),
    "antigravity": ([W], "contract", "not in the hook payload"),
  },
  "stderr": {
    "codex": ([H, W], "reasoned", ""), "copilot": ([W], "absent", "terminal only"),
    "antigravity": ([W], "contract", "not in the hook payload"),
  },
  "sandboxed": {
    "codex":       ([], "absent", "across 49 sessions, nothing arrived"),
    "copilot":     ([], "absent", "across 25 sessions, nothing arrived"),
    "antigravity": ([], "absent", "across 10 sessions, nothing arrived"),
  },
  "file_path": {
    "codex": ([H, F], "seen", ""), "copilot": ([H, F], "seen", ""),
    "antigravity": ([H, F], "seen",
                    "deliverables land in ~/.gemini/antigravity-cli/scratch, "
                    "outside the workspace; point the watcher there too"),
  },
  "old_content": {
    "codex": ([F], "reasoned", ""), "copilot": ([], "reasoned", ""),
    "antigravity": ([], "reasoned", ""),
  },
  "new_content": {
    "codex": ([F], "reasoned", ""), "copilot": ([], "reasoned", ""),
    "antigravity": ([], "reasoned", ""),
  },
  "permission_decision": {
    "codex":       ([], "reasoned", ""),
    "copilot":     ([], "absent",   "requests arrive, decisions do not"),
    "antigravity": ([], "contract", "the decision field is the hook's answer, not the vendor's"),
  },
  "permission_options": {
    "codex": ([], "reasoned", ""),
    "copilot": ([H], "seen", "permissionSuggestions"),
    "antigravity": ([], "reasoned", ""),
  },
  "loop_count": {
    "codex":       ([], "absent", "across 2 sessions, nothing arrived; thin"),
    "copilot":     ([], "reasoned", ""),
    "antigravity": ([H], "seen", "stepIdx and invocationNum"),
  },
  "compaction_reason": {
    "codex": ([], "reasoned", ""), "copilot": ([], "reasoned", ""),
    "antigravity": ([], "reasoned", "no session ran long enough to test"),
  },
  "background_tasks": {
    "codex": ([], "reasoned", ""), "copilot": ([], "reasoned", ""),
    "antigravity": ([H], "seen", "Stop.fullyIdle"),
  },
  "instructions_loaded": {
    "codex":       ([F], "reasoned", "AGENTS.md"),
    "copilot":     ([F], "reasoned", "copilot-instructions.md"),
    "antigravity": ([F], "reasoned", ".agents/ customisation root"),
  },
  "raw_ref": {
    "codex":       ([H, F], "seen",     "rollout file path"),
    "copilot":     ([F],    "reasoned", ""),
    "antigravity": ([H],    "seen",     "transcriptPath and artifactDirectoryPath"),
  },
}

# Fields whose source is the same whatever the tool: computed ones, the
# ones only the file watcher or wrapper can see, and the git collector.
_SAME_FOR_ALL = {
  "is_multi_agent": [D], "delegation_depth": [D], "prompt_word_count": [D],
  "slash_command": [D], "tool_result_size": [D], "tool_is_builtin": [D],
  "risk_markers": [D], "outside_workspace": [D], "sensitivity_tier": [D],
  "violation_count": [D], "escalation_flag": [D], "secret_detected": [D],
  "injection_markers": [D], "turn_count": [D], "chars_added": [D],
  "chars_removed": [D], "lines_added": [D], "lines_removed": [D],
  "operator_username": [W, F], "hostname": [W],
  "git_branch": [F, W], "git_commit": [F, K],
  "file_change_kind": [F], "content_hash": [F], "reverted_to_earlier": [F],
  "file_size": [F], "is_binary": [F], "is_config_file": [F, D],
  "is_dependency_manifest": [F], "is_lockfile": [F],
  "hung_seconds": [W], "is_developer_command": [W],
  "mcp_tools_available": [P], "mcp_server_version": [P], "mcp_transport": [P],
}

NEW_TOOLS = ("codex", "copilot", "antigravity")
EVIDENCE = {}   # (field, tool) -> (basis, note)

for _field, (_tools, _note) in PRECEDENCE.items():
    for _t in NEW_TOOLS:
        if _field in EXTENDED and _t in EXTENDED[_field]:
            order, basis, why = EXTENDED[_field][_t]
        elif _field in _SAME_FOR_ALL:
            order, basis, why = list(_SAME_FOR_ALL[_field]), "reasoned", \
                "same source on every tool"
        else:
            order, basis, why = [], "reasoned", "no collector known to supply it"
        _tools[_t] = order
        EVIDENCE[(_field, _t)] = (basis, why)


def coverage(tool):
    """Fields a tool can supply at all, and on what basis."""
    out = {"seen": 0, "raw": 0, "absent": 0, "contract": 0, "reasoned": 0,
           "supplied": 0, "total": len(PRECEDENCE)}
    for f, (tools, _n) in PRECEDENCE.items():
        if tools.get(tool):
            out["supplied"] += 1
        if (f, tool) in EVIDENCE:
            out[EVIDENCE[(f, tool)][0]] += 1
    return out

def main():
    if "--json" in sys.argv:
        print(json.dumps({k: {"sources": v[0], "note": v[1]}
                          for k, v in PRECEDENCE.items()}, indent=2))
        return 0

    gaps_only = "--gaps" in sys.argv
    cc_only = ccg = cg = both = neither = 0

    print(f"\n{'field':<24} {'Claude Code':<26} {'Cursor':<26} note")
    print("-" * 130)
    for field, (srcs, note) in PRECEDENCE.items():
        cc = srcs.get("claude-code", [])
        cu = srcs.get("cursor", [])
        if gaps_only and (cc or cu):
            continue
        if cc and cu:
            both += 1
        elif cc:
            cc_only += 1
        elif cu:
            cg += 1
        else:
            neither += 1
        f_cc = " > ".join(COLLECTOR_NAMES[c] for c in cc) or "—"
        f_cu = " > ".join(COLLECTOR_NAMES[c] for c in cu) or "—"
        print(f"{field:<24} {f_cc[:25]:<26} {f_cu[:25]:<26} {note[:52]}")

    if not gaps_only:
        print(f"\n{len(PRECEDENCE)} fields")
        print(f"  both tools        {both}")
        print(f"  Claude Code only  {cc_only}")
        print(f"  Cursor only       {cg}")
        print(f"  neither           {neither}")

        from collections import Counter
        wins = Counter()
        for srcs, _ in PRECEDENCE.values():
            for tool, chain in srcs.items():
                if chain:
                    wins[(tool, chain[0])] += 1
        print("\nwinning collector per tool:")
        for tool in ("claude-code", "cursor"):
            row = [(c, n) for (t, c), n in wins.items() if t == tool]
            row.sort(key=lambda x: -x[1])
            print(f"  {tool:<14} " + "  ".join(
                f"{COLLECTOR_NAMES[c]} {n}" for c, n in row))
    return 0


def _print_evidence(tool):
    print(f"\n{tool}: precedence and basis\n")
    print(f"{'field':<24}{'collectors':<34}{'basis':<10}note")
    print("-" * 110)
    for f, (tools, _n) in PRECEDENCE.items():
        order = tools.get(tool, [])
        basis, why = EVIDENCE.get((f, tool), ("original", ""))
        print(f"{f:<24}{(', '.join(order) or '—'):<34}{basis:<10}{why[:60]}")
    c = coverage(tool)
    print(f"\n{c['supplied']} of {c['total']} fields have a source. "
          f"seen {c['seen']}, in raw but unread {c['raw']}, "
          f"confirmed absent {c['absent']}, from vendor docs {c['contract']}, "
          f"reasoned {c['reasoned']}")


if __name__ == "__main__":
    if "--evidence" in sys.argv:
        _i = sys.argv.index("--evidence")
        _print_evidence(sys.argv[_i + 1] if _i + 1 < len(sys.argv) else "codex")
        sys.exit(0)
    sys.exit(main())
