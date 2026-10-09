"""Game-emulation and runtime-patching concerns for TaskManager.

These behaviours are unrelated to job orchestration: they patch an installed
game with a local emulation runtime, run an emulator binary, or fix execute
permissions. They only touch TaskManager for a handful of UI/status helpers and
read ``game_data`` / ``main_window``, so they live here as mixins rather than
bloat the orchestrator.

Split out of ``managers/task_manager.py``; the mixins are composed back into
TaskManager so its public API is unchanged.
"""