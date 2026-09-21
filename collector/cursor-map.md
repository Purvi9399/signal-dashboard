
# cursor

16 payloads, 8 event types fired: afterAgentResponse, afterAgentThought, afterShellExecution, beforeShellExecution, beforeSubmitPrompt, postToolUse, preToolUse, stop


## Identity & Correlation

| Observable | Status | Evidence |
|---|---|---|
| Session id | CONFIRMED | Hooks: beforeSubmitPrompt.session_id |
| Conversation id | CONFIRMED | Hooks: beforeSubmitPrompt.conversation_id |
| Generation / turn id | CONFIRMED | Hooks: beforeSubmitPrompt.generation_id |
| Model request id | CONFIRMED | Hooks: beforeSubmitPrompt.generation_id |
| Tool call id | CONFIRMED | Hooks: preToolUse.tool_use_id |
| Workspace roots | CONFIRMED | Hooks: beforeSubmitPrompt.workspace_roots |
| Transcript pointer | CONFIRMED | Hooks: beforeSubmitPrompt.transcript_path |
| Parent session id | NOT SEEN |  |
| Subagent id | NOT SEEN |  |
| Parent agent id | DERIVE | nested agent_id chain |
| User account uuid | NOT SEEN |  |
| Organization id | NOT SEEN |  |
| User email | CONFIRMED | Hooks: beforeSubmitPrompt.user_email |
| User account uuid | NOT SEEN |  |
| User account id | NOT SEEN |  |
| Hashed user id | NOT SEEN |  |
| Surface / terminal type | NOT SEEN |  |
| Tool version | CONFIRMED | Hooks: beforeSubmitPrompt.cursor_version |
| OS type and version | NOT SEEN |  |
| CPU architecture | NOT SEEN |  |
| Event sequence number | NOT SEEN |  |
| OS username | DERIVE | CLI Wrapper / collector environment |
| Hostname | DERIVE | collector environment |

## Session Lifecycle

| Observable | Status | Evidence |
|---|---|---|
| Session start time | NOT SEEN | expected on SessionStart, sessionStart |
| Session end time | NOT SEEN | expected on SessionEnd, sessionEnd |
| Session duration | DERIVE | start and end timestamps |
| Session resumed vs fresh | NOT SEEN | expected on SessionStart |
| Model at session start | NOT SEEN | expected on SessionStart |
| Exit reason | NOT SEEN | expected on SessionEnd |

## Prompt & Input

| Observable | Status | Evidence |
|---|---|---|
| Prompt text | CONFIRMED | Hooks: beforeSubmitPrompt.prompt |
| Prompt length | DERIVE | len of prompt text |
| Prompt word count | DERIVE | split of prompt text |
| Permission mode at prompt | NOT SEEN | expected on UserPromptSubmit |
| Working directory | CONFIRMED | Hooks: preToolUse.cwd |
| Attachment count | CONFIRMED | Hooks: beforeSubmitPrompt.attachments |
| Composer mode (agent/ask/edit) | CONFIRMED | Hooks: beforeSubmitPrompt.composer_mode |
| Slash command used | DERIVE | prompt text starts with / |
| Slash command source | NOT SEEN |  |
| Prompt redacted by default | NOT SEEN | expected on user_prompt |
| Secret-shaped string in prompt | DERIVE | pattern match on prompt text |
| Time of day of prompt | DERIVE | row timestamp |
| Gap since previous prompt | DERIVE | difference of prompt timestamps |
| Similarity to previous prompt | DERIVE | text comparison across prompts |
| Frustration markers | DERIVE | language classification on prompt text |

## Model / LLM Call

| Observable | Status | Evidence |
|---|---|---|
| Model name | CONFIRMED | Hooks: beforeSubmitPrompt.model_id |
| Input tokens | CONFIRMED | Hooks: afterAgentResponse.input_tokens |
| Output tokens | CONFIRMED | Hooks: afterAgentResponse.output_tokens |
| Cache read tokens | CONFIRMED | Hooks: afterAgentResponse.cache_read_tokens |
| Cache creation tokens | CONFIRMED | Hooks: afterAgentResponse.cache_write_tokens |
| Total tokens | DERIVE | sum of input and output |
| Cost in USD | DERIVE | tokens times model rate |
| Thinking / reasoning blocks | DERIVE | transcript content type thinking |
| Effort level | NOT SEEN |  |
| Sampling parameters | CONFIRMED | Hooks: afterAgentThought.model_params |
| Assistant response text | CONFIRMED | Hooks: afterAgentResponse.text |
| Agent reasoning text | CONFIRMED | Hooks: afterAgentThought.text |
| Reasoning step duration | CONFIRMED | Hooks: afterAgentThought.duration_ms |
| Response streamed in parts | NOT SEEN | expected on MessageDisplay |
| Response final flag | NOT SEEN | expected on MessageDisplay |
| Request latency | NOT SEEN | expected on api_request |
| Time to first token | DERIVE | OTel only |
| Stop reason | NOT SEEN | expected on api_request |

## Agent Behavior & Planning

| Observable | Status | Evidence |
|---|---|---|
| Active working time | NOT SEEN | expected on claude_code.active_time.total |
| Turn count in session | DERIVE | count of distinct prompt_id |
| Loop count within a turn | CONFIRMED | Hooks: stop.loop_count |
| Turn outcome status | CONFIRMED | Hooks: stop.status |
| Plan / todo list created | NOT SEEN | expected on TaskCreated |
| Plan items completed | NOT SEEN | expected on TaskCompleted |
| Subagent spawned | NOT SEEN | expected on SubagentStart, subagentStart |
| Subagent completed | NOT SEEN | expected on subagent_completed |
| Subagent type | NOT SEEN | expected on SubagentStart, SubagentStop |
| Subagent transcript | NOT SEEN | expected on SubagentStop |
| Subagent duration | DERIVE | start and stop timestamps per agent_id |
| Delegation depth | DERIVE | nesting of agent_id |
| Context compaction fired | NOT SEEN | expected on PreCompact, preCompact |
| Background tasks running | NOT SEEN | expected on Stop |
| Scheduled jobs on session | NOT SEEN | expected on Stop |
| Instructions / rules loaded | NOT SEEN | expected on InstructionsLoaded |
| MCP server connection | NOT SEEN | expected on mcp_server_connection |
| Session count | NOT SEEN | expected on claude_code.session.count |
| Our own collectors executing | NOT SEEN | expected on hook_execution_complete |
| Collector registration | NOT SEEN | expected on hook_registered |

## Tool Calls

| Observable | Status | Evidence |
|---|---|---|
| Tool name | CONFIRMED | Hooks: preToolUse.tool_name |
| Full tool arguments | CONFIRMED | Hooks: preToolUse.tool_input |
| Tool description | DERIVE | tool_input.description |
| Full tool result | CONFIRMED | Hooks: postToolUse.tool_output |
| Tool duration | CONFIRMED | Hooks: postToolUse.duration |
| Tool success or failure | CONFIRMED | Hooks: postToolUse.tool_output |
| Tool interrupted | DERIVE | tool_response.interrupted |
| Tool error text | DERIVE | tool_response.stderr |
| Tools called in a parallel batch | NOT SEEN | expected on PostToolBatch |
| Batch size | DERIVE | len of tool_calls |
| Built-in vs MCP tool | DERIVE | tool_name against the built-in set |
| Tool sequence within a turn | DERIVE | ordering within one prompt_id |
| Repeated identical tool calls | DERIVE | duplicate tool_input within a session |

## File Operations

| Observable | Status | Evidence |
|---|---|---|
| File path read | DERIVE | tool_input.file_path |
| File path written | DERIVE | tool_input.file_path |
| Full old content | DERIVE | edits[].old_string |
| Full new content | DERIVE | edits[].new_string |
| Characters added and removed | DERIVE | lengths of old and new |
| File changed on disk | DERIVE | FS Watcher |
| Lines of code added or removed | NOT SEEN | expected on claude_code.lines_of_code.count |
| File is a config or secret file | DERIVE | path pattern match |
| File outside the workspace root | DERIVE | path against cwd |

## Shell / Terminal

| Observable | Status | Evidence |
|---|---|---|
| Shell command string | CONFIRMED | Hooks: beforeShellExecution.command |
| stdout content | CONFIRMED | Hooks: afterShellExecution.output |
| stderr content | DERIVE | tool_response.stderr |
| Exit code | CONFIRMED | Hooks: postToolUse.tool_output |
| Command ran sandboxed | CONFIRMED | Hooks: beforeShellExecution.sandbox |
| Command timeout set | CONFIRMED | Hooks: preToolUse.tool_input |
| Command duration | CONFIRMED | Hooks: afterShellExecution.duration |
| Command hung while alive | DERIVE | CLI Wrapper stall detection |
| Risky command markers | DERIVE | pattern match on command |

## Permission & Approval

| Observable | Status | Evidence |
|---|---|---|
| Permission requested | NOT SEEN | expected on PermissionRequest |
| Permission options offered | NOT SEEN | expected on PermissionRequest |
| Permission prompt shown to user | NOT SEEN | expected on Notification |
| Permission granted | DERIVE | PermissionRequest then PreToolUse in same prompt_id |
| Permission denied | DERIVE | PermissionRequest with no matching PreToolUse |
| Denial reason typed by the human | NOT SEEN | expected on PermissionDenied |
| Approval scope once vs always | NOT SEEN |  |
| Permission mode changed | DERIVE | change of permission_mode across events |

## Human Intervention

| Observable | Status | Evidence |
|---|---|---|
| Session abandoned mid-task | DERIVE | reason plus incomplete turn |
| Edit accepted or rejected | NOT SEEN | expected on claude_code.code_edit_tool.decision |
| Undo after an agent edit | DERIVE | FS Watcher content reversion |
| Manual edit after an agent edit | DERIVE | FS Watcher change with no tool event |
| Rapid interrupts / rage quit | DERIVE | CLI Wrapper interrupt events |

---
confirmed from real payloads: 35
derivable from what we have:  44
not seen in this data:        47
