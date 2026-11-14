"""AI Operations Agent (Phase D).

Tool registry with execution-time RBAC + tenant scoping (D1), a
dependency-free state graph (intent → plan → tool loop → validate → respond)
with iteration caps and per-tool timeouts (D2), a persisted human-in-the-loop
approval workflow for side-effecting tools (D3), and the HTTP/WS surface (D4).

Planners: an LLM planner runs when a key is configured; otherwise the
deterministic rule-based planner answers and every response is labeled
``mock-demo`` — never a silent pretend-live path.
"""
