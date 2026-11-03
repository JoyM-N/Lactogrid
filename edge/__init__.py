"""Site gateway: controller, local database, and the closed control loop."""

__all__ = ["Controller", "EdgeStore", "run_closed_loop"]


def __getattr__(name: str):
    if name == "Controller":
        from edge.controller import Controller

        return Controller
    if name == "EdgeStore":
        from edge.store import EdgeStore

        return EdgeStore
    if name == "run_closed_loop":
        from edge.loop import run_closed_loop

        return run_closed_loop
    raise AttributeError(name)
