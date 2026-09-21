#!/bin/bash
# Signal Collect — MCP proxy demo

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
rm -rf signal_sessions
echo "api_key=sk-test-EXAMPLE-not-a-real-key  db_password=hunter2" > .env

REQ='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"read_file","arguments":{"path":".env"}}}'

echo
echo "${BOLD}Signal Collect — MCP proxy${RESET}"
echo "${DIM}Sits between the agent and its tool servers. One line of config.${RESET}"
echo

echo "${BOLD}1. Watching${RESET} — the agent asks a tool server to read a file"
echo "${DIM}   Neither side knows we are in the middle.${RESET}"
echo "$REQ" | SIGNAL_MCP_NAME=demo-files python3 signal_mcp_proxy.py \
  -- python3 mock_mcp_server.py > /dev/null 2>&1
python3 read_rows.py watch

echo
echo "${BOLD}2. Refusing${RESET} — the same request, with a policy rule in place"
echo "${DIM}   SIGNAL_DENY=\".env,id_rsa,credentials\"${RESET}"
echo "$REQ" | SIGNAL_MCP_NAME=demo-files SIGNAL_DENY=".env,id_rsa,credentials" \
  python3 signal_mcp_proxy.py -- python3 mock_mcp_server.py 2>/dev/null \
  | python3 -c "
import sys, json
for line in sys.stdin:
    m = json.loads(line)
    if m.get('id') == 3:
        print('   agent received      ' + json.dumps(m.get('error', m.get('result'))))
"
python3 read_rows.py blocked

echo
echo "${DIM}   The real server never saw that request.${RESET}"
echo "${DIM}   Same row format the classifier and COPS already read.${RESET}"
echo
