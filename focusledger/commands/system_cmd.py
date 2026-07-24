"""`focusledger llm ...` and `focusledger autostart ...` — Phase 8 and Phase 9."""

from __future__ import annotations

import argparse

from focusledger.cli_support import ensure_initialized, print_kv
from focusledger.services import autostart as autostart_service
from focusledger.services import llm as llm_service


def register(subparsers) -> None:
    _register_llm(subparsers)
    _register_autostart(subparsers)


# --------------------------------------------------------------------------
def _register_llm(subparsers) -> None:
    parser = subparsers.add_parser("llm", help="Local model used for daily reviews (Phase 8).")
    sub = parser.add_subparsers(required=True)

    status = sub.add_parser("status", help="Check whether the local model is reachable.")
    status.set_defaults(func=cmd_llm_status)

    test = sub.add_parser("test", help="Send a short prompt to the local model.")
    test.set_defaults(func=cmd_llm_test)


def cmd_llm_status(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    status = llm_service.probe(cfg)
    print("Local LLM")
    print_kv([
        ("enabled", cfg.llm_enabled),
        ("endpoint", status.base_url),
        ("model", status.model),
        ("reachable", status.available or bool(status.installed_models)),
        ("usable", status.available),
    ])
    if status.installed_models:
        print(f"  installed: {', '.join(status.installed_models)}")
    if not status.available:
        print(f"  detail: {status.detail}")
        print()
        print("The daily review still works without a model — it falls back to a")
        print("deterministic version. To enable the written retrospective:")
        print("  1. install Ollama:  https://ollama.com/download")
        print(f"  2. pull a small model:  ollama pull {cfg.llm_model}")
        print(f"     (alternatives: {', '.join(llm_service.SUGGESTED_MODELS)})")
        print("  3. re-run:  focusledger llm status")


def cmd_llm_test(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    try:
        reply = llm_service.generate(
            cfg,
            "You answer in one short sentence.",
            "Reply with exactly: FocusLedger local model is working.",
            timeout=60,
        )
    except llm_service.LlmUnavailable as exc:
        raise SystemExit(f"error: {exc}")
    print(reply)


# --------------------------------------------------------------------------
def _register_autostart(subparsers) -> None:
    parser = subparsers.add_parser("autostart", help="Run the agent at login (Phase 9).")
    sub = parser.add_subparsers(required=True)

    install = sub.add_parser("install", help="Register the agent to start at login.")
    install.set_defaults(func=cmd_autostart_install)

    uninstall = sub.add_parser("uninstall", help="Remove the login item.")
    uninstall.set_defaults(func=cmd_autostart_uninstall)

    status = sub.add_parser("status", help="Show whether autostart is registered.")
    status.set_defaults(func=cmd_autostart_status)


def cmd_autostart_install(args: argparse.Namespace) -> None:
    cfg = ensure_initialized()
    try:
        status = autostart_service.install(cfg)
    except autostart_service.AutostartError as exc:
        raise SystemExit(f"error: {exc}")
    print(f"Autostart installed for {status.platform_name}.")
    print_kv([("location", status.location), ("state", status.detail)])
    print()
    print("The agent will now start at login. Verify with: focusledger status")


def cmd_autostart_uninstall(args: argparse.Namespace) -> None:
    try:
        status = autostart_service.uninstall()
    except autostart_service.AutostartError as exc:
        raise SystemExit(f"error: {exc}")
    print(f"Autostart {status.detail} ({status.platform_name}).")


def cmd_autostart_status(args: argparse.Namespace) -> None:
    status = autostart_service.status()
    print("Autostart")
    print_kv([
        ("platform", status.platform_name),
        ("supported", status.supported),
        ("installed", status.installed),
        ("location", status.location or "-"),
        ("state", status.detail),
    ])
