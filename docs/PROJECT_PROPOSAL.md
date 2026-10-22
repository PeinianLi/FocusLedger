# Fole Project Proposal

[中文版](PROJECT_PROPOSAL_ZH.md) · Related: [Development phases](DEVELOPMENT_PHASES.md)

## An Open, Personal AI Across Your Devices

## Vision

Fole is an open-source, deeply customizable personal AI assistant. It combines context from a user's computers, maintains a shared understanding of that person, initiates interaction when useful, and performs actions only within explicit authority.

The name comes from **Fo**cus**Le**dger, the project's original foundation.

```text
understand → initiate → collaborate → act → verify
```

Fole is not a chatbot attached to a screen. It is one assistant represented by multiple device-local nodes.

## Core Goals

- provide one continuous assistant across a user's trusted computers, with other device types added later;
- combine bounded signals into a user-level context without centralizing raw surveillance data;
- preserve conversations, goals, commitments, preferences, and relevant history;
- decide whether to stay silent, surface context, ask, suggest, or request an action;
- route interaction to the most appropriate device;
- execute narrowly authorized actions and verify their results;
- let users configure models, memory, personality, sensors, policies, and tools;
- remain useful locally and offline.

## Multi-Device Model

Each computer runs a **Fole Node** responsible for local sensing, interaction, and tools. A user-owned **Fole Home** coordinates shared state. Devices are observation sources; the user is the subject being understood.

```text
Home PC Node ────┐
                 ├── Fole Home ── One user timeline and current state
School PC Node ──┘
```

Nodes publish structured, minimal events such as:

```text
device available
work session active
conversation updated
goal progress observed
notification delivered
authorized action completed
```

Raw content stays on its source device by default. Shared records include provenance, confidence, sensitivity, retention, and device identity. Offline nodes synchronize when they reconnect, and conflicting updates remain inspectable.

Fole should merge both computers' footprints into one user timeline. It must not mistake device boundaries for user boundaries, or simultaneous device activity for duplicate work.

## Interaction Model

Fole uses graduated participation:

| Level | Behavior |
|---:|---|
| 0 | Stay silent |
| 1 | Update ambient state |
| 2 | Offer a suggestion |
| 3 | Ask a clarifying question |
| 4 | Request approval for an action |
| 5 | Perform a pre-authorized action |

Proactivity depends on evidence, urgency, likely benefit, interruption cost, recent interactions, current device, and user policy. A cross-device notification must be deduplicated and delivered only where it is useful.

## Safety and Ownership

- Raw data remains local unless the user explicitly chooses otherwise.
- Every sensor, memory category, model, and tool can be inspected and disabled.
- Permissions are scoped by action, resource, device, condition, and duration.
- The model cannot grant itself authority.
- Sensitive or irreversible actions require explicit approval.
- Actions are previewed, verified, and reversible where possible.
- Users can pause sensing, revoke access, delete memory, export data, or stop all actions.
- Learned preferences never override hard privacy or permission rules.

## Architecture

```text
Device Nodes
  local sensors, local memory, UI, device tools
        ↓
Personal Context Fabric
  identity, event sync, provenance, privacy, conflict handling
        ↓
Understanding and Policy
  goals, hypotheses, timing, interruption budget, device routing
        ↓
Interaction and Action Runtime
  conversation, approval, typed tools, execution, verification
        ↓
Outcome and Learning
  feedback, corrections, receipts, replay, personalization
```

Deterministic code owns identity, synchronization, permissions, validation, routing, and execution. Models assist with interpretation, planning, and language.

## Starting Point: FocusLedger

Fole does not start from nothing. FocusLedger, the working code in this repository, is already a single-device version of a Fole Node:

| Fole concept | What FocusLedger provides today |
|---|---|
| Local sensing | A background agent that records the foreground app, window title and keyboard/mouse idle time every second (Windows and macOS) |
| Local memory | One SQLite file with a raw layer kept for a few days and an archived layer kept indefinitely; nothing leaves the machine |
| Understanding | Attention states and effective time, activity categories, workdays, and time attributed to goals |
| Conversation | A local model that answers questions through read-only tools, with every number checked against the data |
| Restraint | Distraction alerts with thresholds and cooldowns, and a recorded reason for each interruption |
| Inspection | A live report of every tracked day, with each tool call behind an answer open to inspection |

What it lacks is everything that makes Fole more than one machine: device pairing, a Fole Home, synchronized user-level state, cross-device routing, and a permission system for actions. The [development phases](DEVELOPMENT_PHASES.md) build those on top of this base.

## Initial Scope

The first version targets one user with two trusted computers: one at home and one at school. It will support pairing, encrypted event synchronization, a unified timeline, shared goals and memory, context-aware interaction routing, and a small set of reversible tools. Phones and other device types come later.

Deferred work includes team accounts, unrestricted computer control, covert sensing, ad-driven cloud services, and autonomous high-risk actions.

## Success

Fole succeeds when a user can move between devices without repeatedly rebuilding context; receive one timely and explainable intervention instead of several noisy alerts; correct the assistant's understanding; and delegate a bounded action with confidence that it will be authorized, verified, and recorded.

The objective is not maximum observation or automation. It is a continuous, user-owned relationship with an AI that is aware enough to help and restrained enough to trust.
