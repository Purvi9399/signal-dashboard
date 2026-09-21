
# claude-code

959 payloads, 29 event types fired: MessageDisplay, Notification, PermissionRequest, PostToolBatch, PostToolUse, PreToolUse, SessionEnd, SessionStart, Stop, SubagentStart, SubagentStop, UserPromptSubmit, [OTel] api_request, [OTel] assistant_response, [OTel] auth, [OTel] claude_code.active_time.total, [OTel] claude_code.code_edit_tool.decision, [OTel] claude_code.cost.usage, [OTel] claude_code.lines_of_code.count, [OTel] claude_code.session.count, [OTel] claude_code.token.usage, [OTel] hook_execution_complete, [OTel] hook_execution_start, [OTel] hook_registered, [OTel] mcp_server_connection, [OTel] subagent_completed, [OTel] tool_decision, [OTel] tool_result, [OTel] user_prompt


## Identity & Correlation

| Observable | Status | Evidence |
|---|---|---|
| Session id | CONFIRMED | Hooks: SessionStart.session_id |
| Conversation id | CONFIRMED | Hooks: SessionStart.session_id |
| Generation / turn id | CONFIRMED | Hooks: MessageDisplay.turn_id |
| Model request id | CONFIRMED | Hooks: MessageDisplay.message_id |
| Tool call id | CONFIRMED | Hooks: PreToolUse.tool_use_id |
| Parent session id | NOT SEEN |  |
| Subagent id | CONFIRMED | Hooks: PreToolUse.agent_id |
| Parent agent id | DERIVE | nested agent_id chain |
| User account uuid | NOT SEEN |  |
| Organization id | NOT SEEN |  |
| User email | CONFIRMED | OTel: auth.user.email |
| User account uuid | CONFIRMED | OTel: auth.user.account_uuid |
| User account id | CONFIRMED | OTel: auth.user.account_id |
| Hashed user id | CONFIRMED | OTel: auth.user.id |
| Surface / terminal type | CONFIRMED | OTel: auth.terminal.type |
| Tool version | CONFIRMED | OTel: auth.service.version |
| OS type and version | CONFIRMED | OTel: auth.os.type |
| CPU architecture | CONFIRMED | OTel: auth.host.arch |
| Event sequence number | CONFIRMED | OTel: auth.event.sequence |
| OS username | DERIVE | CLI Wrapper / collector environment |
| Hostname | DERIVE | collector environment |

## Session Lifecycle

| Observable | Status | Evidence |
|---|---|---|
| Session start time | CONFIRMED | Hooks: SessionStart fired |
| Session end time | CONFIRMED | Hooks: SessionEnd fired |
| Session duration | DERIVE | start and end timestamps |
| Session resumed vs fresh | CONFIRMED | Hooks: SessionStart.source |
| Model at session start | CONFIRMED | Hooks: SessionStart.model |
| Exit reason | CONFIRMED | Hooks: SessionEnd.reason |

## Prompt & Input

| Observable | Status | Evidence |
|---|---|---|
| Prompt text | CONFIRMED | Hooks: UserPromptSubmit.prompt |
| Prompt length | DERIVE | len of prompt text |
| Prompt word count | DERIVE | split of prompt text |
| Permission mode at prompt | CONFIRMED | Hooks: UserPromptSubmit.permission_mode |
| Working directory | CONFIRMED | Hooks: SessionStart.cwd |
| Attachment count | NOT SEEN |  |
| Slash command used | CONFIRMED | OTel: user_prompt.command_name |
| Slash command source | CONFIRMED | OTel: user_prompt.command_source |
| Prompt redacted by default | NOT SEEN | expected on user_prompt |
| Secret-shaped string in prompt | DERIVE | pattern match on prompt text |
| Time of day of prompt | DERIVE | row timestamp |
| Gap since previous prompt | DERIVE | difference of prompt timestamps |
| Similarity to previous prompt | DERIVE | text comparison across prompts |
| Frustration markers | DERIVE | language classification on prompt text |

## Model / LLM Call

| Observable | Status | Evidence |
|---|---|---|
| Model name | CONFIRMED | Hooks: SessionStart.model |
| Input tokens | DERIVE | transcript usage.input_tokens |
| Output tokens | DERIVE | transcript usage.output_tokens |
| Cache read tokens | DERIVE | transcript usage.cache_read_input_tokens |
| Cache creation tokens | DERIVE | transcript usage.cache_creation_input_tokens |
| Total tokens | DERIVE | sum from transcript |
| Cost in USD | DERIVE | tokens times model rate, no direct field |
| Thinking / reasoning blocks | DERIVE | transcript content type thinking |
| Effort level | CONFIRMED | Hooks: PreToolUse.effort |
| Assistant response text | CONFIRMED | Hooks: MessageDisplay.delta |
| Response streamed in parts | CONFIRMED | Hooks: MessageDisplay.index |
| Response final flag | CONFIRMED | Hooks: MessageDisplay.final |
| Request latency | DERIVE | OTel only |
| Time to first token | DERIVE | OTel only |
| Stop reason | DERIVE | OTel only |

## Agent Behavior & Planning

| Observable | Status | Evidence |
|---|---|---|
| Active working time | NOT SEEN | expected on claude_code.active_time.total |
| Turn count in session | DERIVE | count of distinct prompt_id |
| Loop count within a turn | DERIVE | count events per prompt_id |
| Plan / todo list created | NOT SEEN | expected on TaskCreated |
| Plan items completed | NOT SEEN | expected on TaskCompleted |
| Subagent spawned | CONFIRMED | Hooks: SubagentStart fired |
| Subagent type | CONFIRMED | Hooks: SubagentStart.agent_type |
| Subagent transcript | CONFIRMED | Hooks: SubagentStop.agent_transcript_path |
| Subagent duration | DERIVE | start and stop timestamps per agent_id |
| Delegation depth | DERIVE | nesting of agent_id |
| Context compaction fired | NOT SEEN | expected on PreCompact, preCompact |
| Background tasks running | CONFIRMED | Hooks: Stop.background_tasks |
| Scheduled jobs on session | CONFIRMED | Hooks: Stop.session_crons |
| Instructions / rules loaded | NOT SEEN | expected on InstructionsLoaded |

## Tool Calls

| Observable | Status | Evidence |
|---|---|---|
| Tool name | CONFIRMED | Hooks: PreToolUse.tool_name |
| Full tool arguments | CONFIRMED | Hooks: PreToolUse.tool_input |
| Tool description | CONFIRMED | Hooks: PreToolUse.tool_input |
| Full tool result | CONFIRMED | Hooks: PostToolUse.tool_response |
| Tool duration | CONFIRMED | Hooks: PostToolUse.duration_ms |
| Tool success or failure | CONFIRMED | Hooks: PostToolUse.tool_response |
| Tool interrupted | CONFIRMED | Hooks: PostToolUse.tool_response |
| Tool error text | CONFIRMED | Hooks: PostToolUse.tool_response |
| Tools called in a parallel batch | CONFIRMED | Hooks: PostToolBatch.tool_calls |
| Batch size | CONFIRMED | Hooks: PostToolBatch.tool_calls |
| Built-in vs MCP tool | DERIVE | tool_name against the built-in set |
| Tool sequence within a turn | DERIVE | ordering within one prompt_id |
| Repeated identical tool calls | DERIVE | duplicate tool_input within a session |

## File Operations

| Observable | Status | Evidence |
|---|---|---|
| File path read | CONFIRMED | Hooks: PreToolUse.tool_input |
| File path written | CONFIRMED | Hooks: PostToolUse.tool_input |
| Full old content | DERIVE | edits[].old_string |
| Full new content | DERIVE | edits[].new_string |
| Characters added and removed | DERIVE | lengths of old and new |
| File changed on disk | DERIVE | FS Watcher |
| File is a config or secret file | DERIVE | path pattern match |
| File outside the workspace root | DERIVE | path against cwd |

## Shell / Terminal

| Observable | Status | Evidence |
|---|---|---|
| Shell command string | CONFIRMED | Hooks: PreToolUse.tool_input |
| stdout content | CONFIRMED | Hooks: PostToolUse.tool_response |
| stderr content | CONFIRMED | Hooks: PostToolUse.tool_response |
| Exit code | DERIVE | CLI Wrapper on Claude Code |
| Command duration | CONFIRMED | Hooks: PostToolUse.duration_ms |
| Command hung while alive | DERIVE | CLI Wrapper stall detection |
| Risky command markers | DERIVE | pattern match on command |

## Permission & Approval

| Observable | Status | Evidence |
|---|---|---|
| Permission requested | CONFIRMED | Hooks: PermissionRequest fired |
| Permission options offered | CONFIRMED | Hooks: PermissionRequest.permission_suggestions |
| Permission prompt shown to user | CONFIRMED | Hooks: Notification.notification_type |
| Permission granted | DERIVE | PermissionRequest followed by PreToolUse in same prompt_id |
| Permission denied | DERIVE | PermissionRequest with no matching PreToolUse |
| Denial reason typed by the human | NOT SEEN | expected on PermissionDenied |
| Approval scope once vs always | CONFIRMED | Hooks: UserPromptSubmit.permission_mode |
| Permission mode changed | DERIVE | change of permission_mode across events |

## Human Intervention

| Observable | Status | Evidence |
|---|---|---|
| Session abandoned mid-task | CONFIRMED | Hooks: SessionEnd.reason |
| Undo after an agent edit | DERIVE | FS Watcher content reversion |
| Manual edit after an agent edit | DERIVE | FS Watcher change with no tool event |
| Rapid interrupts / rage quit | DERIVE | CLI Wrapper interrupt events |

---
confirmed from real payloads: 56
derivable from what we have:  43
not seen in this data:        11
