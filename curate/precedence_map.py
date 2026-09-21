#!/usr/bin/env python3
"""
Project Signal — field precedence map

For every field in the observables table, this says which collector supplies
it, in preference order, per tool. The curation layer reads this to decide
who wins when several collectors report the same thing.

Order matters: first entry that has a value wins. Later entries are still
recorded in `contributing`, and a mismatch sets collectors_agree = false.

Verified against real captures on Claude Code 2.1.220 and Cursor 3.13.25,
July and August 2026. Entries marked UNVERIFIED are reasoned from the field
existing, not from having seen it arrive.

    python3 precedence_map.py              # readable table
    python3 precedence_map.py --json       # machine readable
    python3 precedence_map.py --gaps       # fields no collector supplies
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


if __name__ == "__main__":
    sys.exit(main())
