# TextCorrector Design Spec: Instant Cancellation & Editable Templates

## Problem Statement

1. **Resource Blocking:** When the popup opens, a background autocorrect immediately starts. If the user clicks a template, sends a chat message, or clicks Reset, the autocorrect process is not killed aggressively enough. Because the `requests.post` call is blocking, it hogs the `llama-server` queue until it finishes processing the current sentence, severely delaying the chat/template request.
2. **Template Rigidity & Quality:** The hardcoded "Email" template alters wording too much and adds unwanted greetings. Furthermore, default templates are locked in code; users can only add new ones, but cannot edit or delete existing ones, nor can they view/modify the underlying prompts.

## Proposed Solution (Approach B)

### 1. Instant HTTP Cancellation
To ensure the autocorrect thread relinquishes the server immediately when a user acts:
*   **Dependency Update:** We will keep using `requests` but we will wrap the request in a `requests.Session`.
*   **Socket Teardown:** We will implement an aggressive cancellation mechanism. When the `_cancel_event` is set (via Reset, clicking a Template, or sending a Chat), we will invoke a teardown on the active request. Since standard `requests.post` doesn't support async cancellation, we can utilize `requests.Session` and close the underlying adapters (`session.close()`), which severs the active TCP socket.
*   **llama-server Behavior:** When the TCP socket is closed by the client mid-generation, `llama-server` detects the broken pipe and immediately aborts the current slot's generation, freeing up the compute resource for the incoming chat request.

### 2. Full Template Customization
To solve the rigid template issue:
*   **Data Migration:** The `CORE_TEMPLATES` constant will be removed. All templates (defaults and user additions) will be stored in `config.json` under `custom_templates`. 
*   **Sensible Defaults:** If `custom_templates` is empty (e.g. fresh install), it will be populated with improved defaults. The "Email" default will be revised: *"Polish this text for a professional email. Do not add greetings or closings if they are not already present. Preserve the user's core wording."*
*   **UI Revamp:** 
    *   The template area in the `CorrectionWindow` will be changed. Instead of simple push buttons, each template will have a small "Edit" (✏️) icon next to it, similar to the Hotkey Edit layout.
    *   Clicking "Edit" will open a Qt dialog where the user can modify both the **Name** and the hidden **Prompt** of the template, or click a **Delete** button to remove it entirely.
*   **State Sync:** Changes made in the UI will immediately save to `config.json` and refresh the view.

## Implementation Plan
1. **Parallel Verification:** If possible during implementation, use parallel subagents to write the unit tests for template serialization and socket cancellation independently of modifying the core UI logic.
2. **Phase 1: Cancellation:** Implement `InterruptibleSession` in `ModelManager`, wire the abort mechanisms, and test that `llama-server` aborts properly on reset/chat.
3. **Phase 2: Templates:** Refactor `ConfigManager` to handle default population, update `CorrectionWindow._refresh_templates()` UI with edit/delete flows, and verify JSON state persistence.

## Risks & Tradeoffs
*   Closing the session adapter mid-flight will raise a `requests.exceptions.ConnectionError` in the thread. We must catch this explicitly and gracefully handle it as a cancellation rather than treating it as a total failure that spawns an error popup.
*   The UI for editing templates needs to be compact so it doesn't clutter the correction window. A clean dialog box keeps the main UI tidy.
