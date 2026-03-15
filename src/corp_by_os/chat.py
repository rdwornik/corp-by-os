"""Interactive terminal chat loop.

Rich-based input/output. Maintains conversation context.
Routes via intent_router -> workflow_engine.
"""

from __future__ import annotations

import logging
import shlex

from rich.console import Console
from rich.panel import Panel

from corp_by_os.intent_router import Intent, route
from corp_by_os.workflow_engine import execute_workflow, load_workflows

logger = logging.getLogger(__name__)
console = Console()

WELCOME_BANNER = """\
[bold]Mów naturalnie po polsku lub angielsku.[/bold]
Zarządzam projektami, prezentacjami, zadaniami.

[dim]Przykłady:[/dim]
  [cyan]"Nowe opportunity, Siemens, WMS"[/cyan]
  [cyan]"Przygotuj prezentację demo dla Lenzing"[/cyan]
  [cyan]"Co wymaga mojej uwagi?"[/cyan]
  [cyan]"Co mam do zrobienia?"[/cyan]

[dim]quit = wyjście · help = pomoc · !cmd = bezpośredni[/dim]\
"""

QUIT_COMMANDS = {"quit", "exit", "q", "wyjdz", "koniec"}


def chat_loop(use_llm: bool = True) -> None:
    """Main interactive chat loop.

    Args:
        use_llm: If False, disable LLM fallback (keyword matching only).
    """
    workflows = load_workflows()

    if not workflows:
        console.print("[red]No workflows loaded. Check config/workflows.yaml[/red]")
        return

    console.print(Panel(WELCOME_BANNER, title="Corp-by-os", border_style="blue"))

    history: list[dict[str, str]] = []
    context = {"history": history}

    while True:
        try:
            user_input = console.input("\n[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Do zobaczenia![/dim]")
            break

        if not user_input:
            continue

        # Check for quit
        if user_input.lower() in QUIT_COMMANDS:
            console.print("[dim]Do zobaczenia![/dim]")
            break

        # Check for special commands
        if _handle_special_command(user_input, workflows):
            continue

        # Route intent
        intent = route(user_input, workflows, context, use_llm=use_llm)

        # Handle result
        _handle_intent(intent, workflows, history)

        # Store turn
        history.append(
            {
                "user": user_input,
                "workflow": intent.workflow_id or "",
                "response": intent.response_text or "",
            }
        )

        # Keep last 10 turns
        if len(history) > 10:
            history[:] = history[-10:]


def _handle_special_command(user_input: str, workflows: dict) -> bool:
    """Handle special commands. Returns True if handled."""
    lower = user_input.lower().strip()

    if lower == "help":
        _show_help(workflows)
        return True

    if lower == "status":
        _show_status()
        return True

    if lower.startswith("!"):
        _run_direct_command(user_input[1:].strip())
        return True

    return False


def _handle_intent(intent: Intent, workflows: dict, history: list) -> None:
    """Process a routing intent — preview, confirm, execute."""
    if intent.workflow_id is None:
        # Chitchat or unclear
        response = intent.response_text or "Nie rozumiem. Wpisz 'help' po pomoc."
        console.print(f"\n[dim]Agent:[/dim] {response}")
        return

    if intent.workflow_id not in workflows:
        console.print(f"\n[yellow]Nieznany workflow: {intent.workflow_id}[/yellow]")
        return

    wf = workflows[intent.workflow_id]
    params = intent.parameters

    # Resolve project path if needed
    if "project" in params:
        try:
            from corp_by_os.project_resolver import resolve_project

            resolved = resolve_project(params["project"])
            if resolved and resolved.onedrive_path:
                params["project_path"] = str(resolved.onedrive_path)
        except Exception:
            pass

    # Show what we're about to do
    source_tag = f"[dim]({intent.source}, {intent.confidence:.0%})[/dim]"
    console.print(f"\n[bold]Zamierzam wykonać:[/bold] {wf.description} {source_tag}")

    if params:
        for k, v in params.items():
            if not k.startswith("_"):
                console.print(f"  [cyan]{k}:[/cyan] {v}")

    # Confirmation for destructive workflows
    if wf.confirmation:
        try:
            answer = console.input("  [bold]Kontynuować? [Y/n][/bold] ").strip().lower()
            if answer and answer not in ("y", "yes", "t", "tak"):
                console.print("[yellow]Anulowano.[/yellow]")
                return
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Anulowano.[/yellow]")
            return

    # Execute
    result = execute_workflow(wf, params)

    # Show results
    for step in result.steps:
        status = "[green]OK[/green]" if step.success else "[red]FAIL[/red]"
        duration = f"({step.duration_seconds:.1f}s)" if step.duration_seconds > 0 else ""
        console.print(
            f"  Step {step.step_index + 1}/{len(wf.steps)}:"
            f" {step.description}... {status} {duration}"
        )
        if step.output and step.success:
            # Show output for informational steps
            if any(kw in wf.id for kw in ("task", "attention", "brief")):
                for line in step.output.split("\n"):
                    console.print(f"    [dim]{line}[/dim]")
        if step.error:
            console.print(f"    [red]{step.error}[/red]")

    if result.success:
        console.print(f"\n[green]Gotowe[/green] ({result.duration_seconds:.1f}s)")
    else:
        console.print("\n[red]Nie udało się.[/red]")


def _show_help(workflows: dict) -> None:
    """Show available workflows."""
    lines = ["[bold]Dostępne komendy:[/bold]\n"]
    for wf_id, wf in sorted(workflows.items()):
        cost = f" ({wf.cost_estimate})" if wf.cost_estimate else ""
        lines.append(f"  [cyan]{wf_id}[/cyan] — {wf.description}{cost}")
        if wf.trigger_phrases:
            phrases = ", ".join(f'"{p}"' for p in wf.trigger_phrases[:3])
            lines.append(f"    [dim]Frazy: {phrases}[/dim]")

    lines.append("\n[bold]Specjalne:[/bold]")
    lines.append("  [cyan]help[/cyan] — ta pomoc")
    lines.append("  [cyan]status[/cyan] — podsumowanie")
    lines.append("  [cyan]!<cmd>[/cyan] — przekieruj do corp CLI")
    lines.append("  [cyan]quit[/cyan] — wyjście")

    console.print(Panel("\n".join(lines), title="Pomoc", border_style="blue"))


def _show_status() -> None:
    """Show quick status summary."""
    try:
        from corp_by_os.task_manager import list_tasks
        from corp_by_os.vault_io import list_projects

        projects = list_projects()
        tasks = list_tasks(status_filter="todo")

        console.print(f"\n  Projekty: [cyan]{len(projects)}[/cyan]")
        console.print(f"  Zadania (todo): [cyan]{len(tasks)}[/cyan]")

        if tasks:
            high = [t for t in tasks if t.priority.value == "high"]
            if high:
                console.print(f"  [red]Pilne: {len(high)}[/red]")
    except Exception as e:
        console.print(f"[yellow]Nie mogę pobrać statusu: {e}[/yellow]")


def _run_direct_command(cmd_str: str) -> None:
    """Run a corp CLI command directly."""
    import subprocess

    full_cmd = f"corp {cmd_str}"
    console.print(f"[dim]> {full_cmd}[/dim]")

    try:
        subprocess.run(
            ["corp"] + shlex.split(cmd_str),
            capture_output=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError:
        console.print("[red]'corp' nie znalezione na PATH[/red]")
    except subprocess.TimeoutExpired:
        console.print("[yellow]Timeout (60s)[/yellow]")
    except Exception as e:
        console.print(f"[red]Błąd: {e}[/red]")
