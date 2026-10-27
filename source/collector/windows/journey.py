"""Presentation derived from existing collection and immutable run authority."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Journey:
    stage: str
    step: int
    heading: str
    detail: str
    action: str | None = None


def attention(state, native, step, heading, detail):
    if state.running and (state.mode == "inspect" or state._stopping or state.phase == "error"
                          or native is not None and native.stop_requested):
        action, next_step = "wait", "Keep this app open while the local helper stops and temporary data is checked."
    elif not state.running and state.residue:
        action, next_step = "clear", "Remove the marked temporary data before returning to your account."
    elif not state.running and (state.needs_inspection or not state.inspected):
        action, next_step = "inspect", "Check temporary data before returning to your account."
    elif native is not None and native.current and (native.phase.endswith("_uncertain") or native.authority == "paused"):
        action, next_step = "retry", "Retry confirmation. Your reviewed selection stays here."
    else:
        action, next_step = "return", "Return to your original account tab to review this outcome. Start a new collection there if needed; it requires a new approval."
    return Journey("attention", step, heading, f"{detail}\n\n{next_step}", action)


def journey(state, native):
    if state.saved_acknowledged or native is not None and native.completion is not None:
        return Journey("complete", 3, "Saved to your account", "The matching account-save receipt has arrived. Open your report when you’re ready.")
    if native is not None and native.active and native.phase in ("cancelled", "failed", "refused", "handoff_expired"):
        step = 3 if state._capture_received else 1 if native.started else 0
        heading = "Collection could not finish" if native.phase == "failed" else "Collection stopped" if native.phase == "cancelled" else "Return to your account"
        if native.phase == "cancelled" and state.running and state.mode == "connect":
            heading = "Stopping collection"
        if native.phase == "failed" and native.started and state.collection_outcome == "failed" and any(
                item["source"] == "messages" and item["reason"] in {
                    "selected_payload_length", "selected_payload_crypto", "selected_payload_invalid", "source_integrity"}
                for item in state.source_diagnostics):
            heading = "Messages could not be validated"
        detail = native.message
        if native.phase == "failed" and state.failure_summary:
            detail += "\n\n" + state.failure_summary
        return attention(state, native, step, heading, detail)
    if native is not None and native.run is not None and not native.current:
        return attention(state, native, 3 if state._capture_received else 1, "Collection approval expired", "A new transfer cannot start with expired approval.")
    if state.phase in ("error", "cancelling"):
        detail = state.status
        if state.phase == "error" and state.failure_summary:
            detail += "\n\n" + state.failure_summary
        return attention(state, native, 1, "Collection needs attention", detail)
    if state._capture_received:
        if native is not None and native.active:
            if native.phase.endswith("_uncertain") or native.authority == "paused" and native.pending is None:
                return attention(state, native, 3, "Confirmation paused", "Your local review is retained. No new transfer is authorized while confirmation is paused.")
        if state.save_receipt_pending:
            return Journey("save", 3, "Waiting for save confirmation", "An account-save receipt is pending. Keep this app and the account tab open until the matching acknowledgment arrives.")
        if state._browser_received:
            return Journey("save", 3, "Waiting for account save", "Your account received the preview. Keep both windows open until the matching account-save acknowledgment arrives.")
        if native is not None and native.continued_selection is not None:
            if native.phase == "bound":
                return Journey("save", 3, "Continue in your account", "Your reviewed selection is connected to the approved account. Return to the original account tab to finish.")
            return Journey("save", 3, "Preparing your reviewed selection", "Confirming the selected contacts and account connection. Account save is not confirmed yet.")
        if state.handoff_id:
            return Journey("save", 3, "Connect your account", "Your local review is ready. Connect the signed-in account, confirm its destination and review the preview before saving. A temporary code is available in Details & recovery.")
        return Journey("contacts", 2, "Choose contacts", "Choose the contacts to include, then continue with their available call and message metadata.")
    if state.running and state.mode == "connect":
        return Journey("collection", 1, "Collecting from your iPhone", "Your backup stays on this PC. After collection, you’ll choose contacts before continuing to your account.")
    if state.residue or state.needs_inspection and not state.running:
        return attention(state, native, 0, "Check temporary data", state.status)
    if native is not None and native.active:
        if native.phase in ("prepared", "starting", "start_uncertain"):
            return Journey("prepare", 1, "Connect and check your iPhone", "Connect and unlock your iPhone, then accept its Trust prompt. Choose Start here: AMPLIFai creates a temporary backup on this PC, then lets you choose contacts. No manual backup is required.")
        return Journey("approval", 0, "Approve this phone collection", "Confirm the account, course and sources before approving this one collection.")
    return Journey("account", 0, "Start from your AMPLIFai account", "Open Phone in your signed-in account and choose Connect phone. This app will receive the account and course automatically.")
