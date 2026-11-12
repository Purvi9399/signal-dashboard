# The task suites under tasks/ are the Harbor tasks' own tests and verifiers. They run inside
# each task's Docker container (harbor run), against /app, not in the repository's test run.
collect_ignore_glob = ["tasks/*"]
