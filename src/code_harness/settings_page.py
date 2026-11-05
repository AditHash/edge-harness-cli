"""The settings page: `edge-harness --settings` or `/settings` in chat.

Values are saved in `<state>/settings.json` through the launcher, which validates each one. Nothing
in a repository can reach this file, and credentials are never stored here. An environment variable
still wins over a saved value, so such a field is shown read-only.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    help: str
    kind: str = "text"  # "text", "bool" or "choice"
    choices: tuple[str, ...] = ()


GROUPS: tuple[tuple[str, tuple[FieldSpec, ...]], ...] = (
    (
        "Model",
        (
            FieldSpec("model", "Model", "A Bedrock model id, or auto to route.", "choice"),
            FieldSpec(
                "residency",
                "Residency",
                "Where auto may route: India, Mumbai, or both.",
                "choice",
                ("india", "mumbai", "any"),
            ),
            FieldSpec("region", "AWS region", "For example ap-south-1."),
            FieldSpec("profile", "AWS profile", "A name from your AWS config, not a credential."),
            FieldSpec("max_tokens", "Max output tokens", "256 to 65536. Default 16384."),
            FieldSpec("compact_threshold", "Auto compact at", "Share of context, 0.2 to 0.95."),
        ),
    ),
    (
        "Cost",
        (
            FieldSpec(
                "session_budget",
                "Session budget (USD)",
                "Warns at 80% and 100% of the estimate. Nothing is stopped. Empty for none.",
            ),
            FieldSpec(
                "input_price",
                "Input price per 1M",
                "Your own rate for every model. Empty uses AWS prices. Needs input and output.",
            ),
            FieldSpec("output_price", "Output price per 1M", "Used with the input price."),
            FieldSpec("cached_price", "Cached price per 1M", "Empty charges the input rate."),
        ),
    ),
    (
        "Interface",
        (FieldSpec("show_thinking", "Show thinking text", "Off shows a spinner instead.", "bool"),),
    ),
    (
        "Tools",
        (
            FieldSpec("enable_subagents", "Subagents", "Lets the model spawn subagents.", "bool"),
            FieldSpec("subagent_max_parallel", "Subagents at once", "1 to 32. Default 8."),
            FieldSpec(
                "subagent_turn_tokens",
                "Subagent tokens per turn",
                "Stops new subagents after this many. Default 2000000. 0 removes the cap.",
            ),
            FieldSpec("enable_web", "Web fetch", "Reads public pages, with approval.", "bool"),
            FieldSpec(
                "enable_host_commands",
                "Real machine commands",
                "run_on_host, with a typed yes per command. Full access to your machine.",
                "bool",
            ),
        ),
    ),
)
SPECS = {spec.key: spec for _, specs in GROUPS for spec in specs}
_DEFAULT = "(default)"


def _source(key: str, saved: dict[str, str], environ: dict[str, str]) -> str:
    from code_harness.launcher import FIELDS

    value = environ.get(FIELDS[key][0])
    if value and value != saved.get(key):
        return "environment"
    return "saved" if key in saved else "default"


def current_rows(
    saved: dict[str, str], environ: dict[str, str]
) -> list[tuple[str, FieldSpec, str, str]]:
    """(group, field, value, source) for every setting."""
    from code_harness.launcher import FIELDS

    rows = []
    for group, specs in GROUPS:
        for spec in specs:
            source = _source(spec.key, saved, environ)
            value = saved.get(spec.key, "")
            if source == "environment":
                value = environ[FIELDS[spec.key][0]]
            rows.append((group, spec, value, source))
    return rows


def settings_table(saved: dict[str, str], environ: dict[str, str]) -> Table:
    """The settings as a table, for output that is not an interactive terminal."""
    table = Table(title="Settings", title_style="bold", show_edge=False)
    table.add_column("group", style="dim")
    table.add_column("key", style="cyan")
    table.add_column("value")
    table.add_column("from", style="dim")
    table.add_column("meaning", style="dim")
    for group, spec, value, source in current_rows(saved, environ):
        table.add_row(group, spec.key, Text(value or "-"), source, Text(spec.help))
    return table


def model_choices(state_dir: Any, current: str = "") -> list[tuple[str, str]]:
    """(label, value) for the model menu, with the known price; only models with access."""
    from code_harness.catalog import build, load_cache
    from code_harness.pricing import PriceBook

    cache = load_cache(state_dir)
    prices = PriceBook(state_dir)
    choices = [("auto: route across models", "auto")]
    for model in build(cache.get("listing", {}), cache.get("probes", {})):
        if model.tools == "no_access":
            continue
        price = prices.get(model.model_id)
        rate = f"${price.input:g} / ${price.output:g}" if price else "price unknown"
        choices.append((f"{model.model_id}  ({rate})", model.model_id))
    if current and current not in {value for _, value in choices}:
        choices.append((current, current))
    return choices


class SettingsApp(App[dict[str, str | None] | None]):
    """Exits with {key: new value, or None to clear}, or None if cancelled."""

    TITLE = "Edge Harness settings"
    CSS = """
    #form { padding: 0 2; }
    .group { text-style: bold; color: $accent; margin: 1 0 0 0; }
    .row { height: auto; margin: 0; }
    .name { width: 26; padding: 1 1 0 0; }
    .control { width: 42; }
    .help { width: 1fr; color: $text-muted; padding: 1 0 0 2; }
    #message { height: auto; padding: 0 2; color: $warning; }
    #buttons { height: 3; padding: 0 2; }
    #buttons Button { margin-right: 2; }
    .-narrow .name { width: 18; }
    .-narrow .control { width: 1fr; }
    .-narrow .help { display: none; }
    """
    # Below this width the help text is hidden, so the controls stay whole and readable.
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (100, "-wide")]  # noqa: RUF012
    BINDINGS: ClassVar[list[Any]] = [
        ("ctrl+s", "save", "Save"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        saved: dict[str, str],
        environ: dict[str, str],
        models: Iterable[tuple[str, str]] = (),
    ) -> None:
        super().__init__()
        self.saved = saved
        self.environ = environ
        self.models = list(models)

    def _control(self, spec: FieldSpec, value: str, locked: bool) -> Any:
        identifier = f"field-{spec.key}"
        if spec.kind == "text":
            return Input(value=value, id=identifier, classes="control", disabled=locked)
        if spec.kind == "bool":
            options = [(_DEFAULT, ""), ("on", "true"), ("off", "false")]
        elif spec.key == "model":
            options = [(_DEFAULT, ""), *self.models]
        else:
            options = [(_DEFAULT, ""), *((choice, choice) for choice in spec.choices)]
        if value and value not in {option for _, option in options}:
            options.append((value, value))
        return Select(
            options,
            value=value,
            allow_blank=False,
            id=identifier,
            classes="control",
            disabled=locked,
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            last_group = ""
            for group, spec, value, source in current_rows(self.saved, self.environ):
                if group != last_group:
                    yield Label(group, classes="group")
                    last_group = group
                locked = source == "environment"
                note = " Set by an environment variable." if locked else ""
                with Horizontal(classes="row"):
                    yield Label(spec.label, classes="name")
                    yield self._control(spec, value, locked)
                    yield Static(spec.help + note, classes="help", markup=False)
        yield Static("", id="message", markup=False)
        with Horizontal(id="buttons"):
            yield Button("Save", id="save", variant="primary")
            yield Button("Cancel", id="cancel")
        yield Footer()

    def collect(self) -> tuple[dict[str, str | None], list[str]]:
        """The changed values, and a problem for each invalid one."""
        from code_harness.launcher import valid_setting

        changes: dict[str, str | None] = {}
        problems: list[str] = []
        for _, spec, _, source in current_rows(self.saved, self.environ):
            if source == "environment":
                continue
            widget: Any = self.query_one(f"#field-{spec.key}")
            value = str(widget.value or "").strip()
            if value == self.saved.get(spec.key, ""):
                continue
            if value and not valid_setting(spec.key, value):
                problems.append(f"{spec.label}: {value} is not valid. {spec.help}")
            changes[spec.key] = value or None
        return changes, problems

    def action_save(self) -> None:
        changes, problems = self.collect()
        if problems:
            self.query_one("#message", Static).update("\n".join(problems))
            return
        self.exit(changes)

    def action_cancel(self) -> None:
        self.exit(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.action_cancel()
