"""The Effector belt — the Mind's vetted, audited path to acting on the world.

Every action Johnny takes (internal *or* external) runs through one seam: a
proposed ``(tool, args)`` is vetted by the Conscience, executed via the
``ToolRegistry`` only if allowed, and recorded — allow or veto — in the
append-only ``action_log`` written by the Core (``core/audit.py``, FC-1). This
package owns the Mind-side pieces of that path (tools, dispatch, the read model
for the audit trail); the immutable write + the budget gate live in the Core.
"""
