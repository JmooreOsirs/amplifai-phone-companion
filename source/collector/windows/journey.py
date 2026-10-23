"""Presentation derived from existing collection and immutable run authority."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Journey:
    stage: str
    step: int
    heading: str
    detail: str


def journey(state, native):
    if state.saved_acknowledged or native is not None and native.completion is not None:
        return Journey("complete", 3, "Saved to your account", "The matching account-save receipt has arrived. Open your report when you’re ready.")
    if native is not None and native.active and native.phase in ("cancelled", "refused", "handoff_expired"):
        return Journey("attention", 3 if state._capture_received else 0, "Return to your account", native.message or "Use your original account tab to continue or recover this setup.")
    if native is not None and native.run is not None and not native.current:
        return Journey("attention", 3 if state._capture_received else 0, "Collection approval expired", "Return to your account to check this collection. A new transfer cannot start with expired approval.")
    if state.phase in ("error", "cancelling"):
        return Journey("attention", 1, "Collection needs attention", state.status)
    if state._capture_received:
        if native is not None and native.active:
            if native.phase.endswith("_uncertain") or native.authority == "paused" and native.pending is None:
                return Journey("attention", 3, "Confirmation paused", native.message or "Your local review is retained. Retry confirmation or return to the approved account. No new transfer is authorized while confirmation is paused.")
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
        return Journey("attention", 0, "Check temporary data", state.status)
    if native is not None and native.active:
        if native.phase in ("prepared", "starting", "start_uncertain"):
            return Journey("prepare", 1, "Connect and check your iPhone", "Connect your iPhone, unlock it and finish any Apple Devices backup before starting here.")
        return Journey("approval", 0, "Approve this phone collection", "Confirm the account, course and sources before approving this one collection.")
    return Journey("account", 0, "Start from your AMPLIFai account", "Open Phone in your signed-in account and choose Connect phone. This app will receive the account and course automatically.")
