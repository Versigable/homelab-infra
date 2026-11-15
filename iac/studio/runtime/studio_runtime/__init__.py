"""The studio's agent runtime in a cell (vault: 1-Projects/game-studio-platform/notes/step-2c-2-plan).

Root daemons hold what a job may not: the GitLab token (studio-agentd, step 2c-2c), the
editors and the Unity licence (studio-unityd). A job reaches them only through the `studio`
CLI's verbs over local sockets, and the daemons tell who is calling from the kernel
(SO_PEERCRED, then the caller's cgroup), never from what the caller says.
"""

VERSION = "0.1.0"
