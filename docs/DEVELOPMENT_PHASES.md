# Fole Development Phases

[中文版](DEVELOPMENT_PHASES_ZH.md) · Related proposal: [`PROJECT_PROPOSAL.md`](PROJECT_PROPOSAL.md)

Fole develops through three product capabilities:

```text
Phase A: You approach Fole.
Phase B: Fole knows when to approach you.
Phase C: Fole can act for you within explicit authority.
```

The initial deployment is deliberately narrow: one user, one home computer, and one school computer. Cross-computer context fusion is part of the foundation, not a later synchronization feature.

## Starting Point

The phases build on FocusLedger, which already runs as a single-device node. Where each phase stands today:

| Phase | Already in place | Not started |
|---|---|---|
| A — Companion Fabric | Local configuration; a local conversation UI (the analyst in the CLI and the report page); pluggable local models through Ollama; an inspectable timeline; `device_id` on samples; a versioned local schema | Pairing and revocation, Fole Home, encrypted synchronization, shared goals and memory across devices, provenance and conflict handling, a permission registry |
| B — Ambient Intelligence | Opt-in activity and idle sensing; corrections that re-explain history; distraction alerts with thresholds and cooldowns; explanations through recorded tool calls and grounding checks; replayable evaluation (`analyst eval`) | A user-level context from several devices, hypotheses with confidence and expiry, interruption budgets, cross-device routing and deduplication |
| C — Trusted Agency | Read-only tools with typed arguments, which the model can call but never use to change anything | Any action tool, approval and undo, scoped permissions, remote approval, receipts, emergency stop |

## Phase A — Companion Fabric

### Goal

Create one usable personal assistant across the user's home and school computers.

### Build

- Fole identity, configuration, and local conversation UI;
- a device-local node on each computer, with secure pairing and revocation;
- encrypted synchronization through a user-owned Fole Home;
- shared goals, conversations, preferences, and selected memories;
- event provenance, sensitivity, retention, and conflict handling;
- pluggable model providers, personalities, and prompts;
- offline operation and delayed synchronization;
- an inspectable timeline and basic permission registry.

Raw device activity is not synchronized by default. Nodes share structured events or user-approved summaries.

### Complete When

- one user can pair the home and school computers;
- a conversation or goal can continue across them;
- shared records show their source and synchronization state;
- revoked devices can no longer read updates or issue commands;
- the assistant remains usable while Fole Home is unavailable.

## Phase B — Ambient Intelligence

### Goal

Let Fole combine bounded device signals and initiate useful interaction without becoming noisy or invasive.

### Build

- opt-in sensors for activity, availability, tasks, and device state;
- a user-level context assembled from multiple device perspectives;
- evidence-linked hypotheses about progress, blocking, divergence, and commitments;
- corrections, confidence, expiration, and privacy boundaries;
- quiet mode, interruption budgets, cooldowns, and proactive levels;
- cross-device notification deduplication and delivery routing;
- explanations showing why Fole intervened and which signals contributed;
- feedback and replay for evaluating alternative policies.

Fole must distinguish a person from their devices. Concurrent device events are fused rather than treated as separate users.

### Complete When

- Fole can identify the most appropriate device for an interaction;
- one event cannot produce duplicate cross-device interruptions;
- every proactive message is traceable to bounded evidence and policy;
- the user can correct a hypothesis and control future behavior;
- disabling a sensor removes it from subsequent decisions.

## Phase C — Trusted Agency

### Goal

Allow Fole to coordinate and execute useful actions across devices within explicit, revocable authority.

### Build

- a plugin-based registry of typed tools and connectors;
- action preview, approval, execution, verification, and undo;
- permissions scoped by action, resource, device, condition, and duration;
- routing to the node capable of performing an approved action;
- remote approval without silent remote execution;
- idempotency, retries, receipts, and partial-failure handling;
- emergency stop and immediate permission revocation;
- inspectable personalization based on user feedback;
- side-effect-free replay and evaluation.

Initial actions should be local, narrow, and reversible: manage goals, record decisions, prepare changes, schedule reminders, or open approved resources.

### Complete When

- no action runs without a matching permission or approval;
- Fole verifies results on the device that performed the action;
- duplicate requests cannot execute twice;
- offline, expired, or revoked nodes cannot act;
- users can inspect what happened, why, where, and under which authority.

## Invariants

```text
One user may have many devices, but one coherent assistant context.
Raw local data does not become shared data by accident.
No permission means no action.
Expired or revoked permission means no action.
Private context never becomes an unexplained inference.
One event never becomes repeated cross-device interruption.
Replay never produces real side effects.
Model failure never disables deterministic safety controls.
```

## Research Questions

- What is the minimum cross-device context required to be useful?
- How should conflicting or incomplete device signals be fused?
- Which device should receive an intervention, and when?
- When does proactive help become interruption or surveillance?
- How can authority move across devices without becoming ambient remote control?
- What evidence makes users comfortable delegating the same action again?
