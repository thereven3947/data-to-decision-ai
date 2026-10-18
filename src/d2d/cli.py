"""``d2d`` command-line interface."""

from __future__ import annotations

import getpass
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from d2d import __version__
from d2d.api.repo import Ontology
from d2d.api.scope import ROLES, Scope, load_account_policy, load_roles
from d2d.auth import MIN_PASSWORD, AuthError, UsersFile, users_file_for
from d2d.calibration import calibrate, mark_exported
from d2d.config import (
    ConfigError,
    config_dir,
    load_categories,
    load_household,
    load_pipeline,
    parse_utc,
)
from d2d.db import LoadError, load_run
from d2d.db import database_url as default_url
from d2d.enrichment import load_enrichment
from d2d.evaluation import evaluate_changelog, evaluate_linking
from d2d.feedback import route_feedback
from d2d.hitl.review import (
    ReviewError,
    create_case,
    human_label,
    open_queue,
    simulate_reviews,
)
from d2d.hitl.sim_reviewer import label_stories
from d2d.hitl.training_set import (
    build_training_set,
    read_training_set,
    training_set_version,
    write_training_set,
)
from d2d.ml.evaluate import evaluate_classifier
from d2d.ml.infer import classify_new
from d2d.ml.train import Classifier, TrainParams, load_meta, model_version
from d2d.ml.train import save as save_model
from d2d.ml.train import train as train_model
from d2d.notify import load_notify, run_notifications, sender_from_env
from d2d.ops import api_plist, backup_plist, pipeline_plist
from d2d.pipeline.events import run_pipeline
from d2d.pipeline.linking import load_linking
from d2d.pipeline.semantics import load_semantics
from d2d.security import evaluate_security, read_events, score_events, write_alerts
from d2d.simulator import simulate as run_simulation
from d2d.simulator import write_cdc, write_ground_truth, write_snapshots
from d2d.simulator.cdc import mirror_cdc_state, plan_cdc

app = typer.Typer(no_args_is_help=True, help="Data to Decision with AI (D2D)")
eval_app = typer.Typer(no_args_is_help=True, help="Deterministic evals against ground truth.")
app.add_typer(eval_app, name="eval")
review_app = typer.Typer(no_args_is_help=True, help="Stages 5-6: review queue, labels, cases.")
app.add_typer(review_app, name="review")
feedback_app = typer.Typer(no_args_is_help=True, help="Stage 12: route feedback, export evals.")
app.add_typer(feedback_app, name="feedback")
security_app = typer.Typer(
    no_args_is_help=True, help="Unexpected/unauthorized configuration changes (OT security)."
)
app.add_typer(security_app, name="security")
user_app = typer.Typer(
    no_args_is_help=True,
    help="Web app accounts. Stores only password hashes, in a users file outside git.",
)
app.add_typer(user_app, name="user")

DataDir = Annotated[Path, typer.Option(help="Data directory (gitignored).")]
ConfigDir = Annotated[Path | None, typer.Option(help="Config directory (default ./config).")]
DatabaseUrl = Annotated[
    str | None,
    typer.Option(help="SQLAlchemy URL (default D2D_DATABASE_URL or the deploy .env keys)."),
]


def _engine(database_url: str | None) -> Engine:
    try:
        url = make_url(database_url) if database_url else default_url()
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return create_engine(url)


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except (LoadError, ReviewError, ConfigError, SQLAlchemyError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    """Print the d2d version."""
    typer.echo(__version__)


@app.command()
def simulate(
    data_dir: DataDir = Path("data"),
    config: ConfigDir = None,
    days: Annotated[int | None, typer.Option(help="Days to simulate.")] = None,
    seed: Annotated[int | None, typer.Option(help="Random seed.")] = None,
    start: Annotated[str | None, typer.Option(help="Start time (ISO 8601, UTC).")] = None,
    scenario: Annotated[
        list[str] | None, typer.Option(help="Only inject these scenario ids (repeatable).")
    ] = None,
) -> None:
    """Stage 1: write device snapshots and scenario ground truth."""
    cfg_dir = config_dir(config)
    household = load_household(cfg_dir / "household.toml")
    pipeline = load_pipeline(cfg_dir / "pipeline.toml")
    result = run_simulation(
        household,
        start=parse_utc(start) if start else pipeline.start,
        days=days if days is not None else pipeline.days,
        cadence_minutes=pipeline.cadence_minutes,
        seed=seed if seed is not None else pipeline.seed,
        scenario_ids=set(scenario) if scenario else None,
    )
    sim_start = parse_utc(start) if start else pipeline.start
    sim_days = days if days is not None else pipeline.days
    cdc = plan_cdc(
        household,
        start=sim_start,
        days=sim_days,
        seed=seed if seed is not None else pipeline.seed,
        cadence_minutes=pipeline.cadence_minutes,
    )
    mirror_cdc_state(result.rows, cdc.true_rows, household)
    partitions = write_snapshots(result.rows, data_dir)
    cdc_path = write_cdc(cdc.rows, data_dir)
    truth = result.ground_truth + cdc.truth
    gt_path = write_ground_truth(truth, data_dir)
    gt_events = sum(len(s["events"]) for s in truth)
    typer.echo(
        f"{partitions} snapshot runs, {len(result.rows)} rows -> {data_dir / 'snapshots'}\n"
        f"{len(cdc.rows)} CDC events -> {cdc_path}\n"
        f"{len(truth)} scenarios, {gt_events} ground-truth events -> {gt_path}"
    )


@app.command()
def run(data_dir: DataDir = Path("data"), config: ConfigDir = None) -> None:
    """Stages 2-4: semantic changelog, change events and linked change stories."""
    cfg_dir = config_dir(config)
    household = load_household(cfg_dir / "household.toml")
    s = run_pipeline(
        data_dir,
        load_semantics(cfg_dir / "semantics.toml"),
        load_linking(cfg_dir / "linking.toml"),
        household.id,
        household.timezone,
    )
    kinds = ", ".join(f"{k}={v}" for k, v in s.by_kind.items())
    typer.echo(f"{s.snapshot_rows} snapshot rows -> {s.changes} changes -> {s.events} events")
    typer.echo(f"by kind: {kinds}")
    typer.echo(f"{s.stories} change stories")


@app.command()
def load(
    data_dir: DataDir = Path("data"), config: ConfigDir = None, database_url: DatabaseUrl = None
) -> None:
    """Load devices, change events and change stories from `d2d run` into Postgres."""
    household = load_household(config_dir(config) / "household.toml")
    engine = _engine(database_url)
    with _db_errors():
        s = load_run(engine, data_dir, household)
    engine.dispose()
    typer.echo(
        f"{s.devices} devices, {s.events} events, {s.stories} stories -> "
        f"{engine.url.render_as_string(hide_password=True)} "
        f"({s.stories_removed} stale stories removed)"
    )


@review_app.command("queue")
def review_queue(database_url: DatabaseUrl = None) -> None:
    """List open review items (abstained or audited stories)."""
    engine = _engine(database_url)
    with _db_errors(), engine.connect() as conn:
        items = open_queue(conn)
    for i in items:
        suggestion = (
            f"{i['suggested_category']} ({i['suggested_confidence']:.2f})"
            if i["suggested_category"]
            else "-"
        )
        typer.echo(
            f"{i['story_id']}  {i['reason']:<8} {i['window_start']:%Y-%m-%d %H:%M}  "
            f"{','.join(i['device_ids'])}  suggested={suggestion}"
        )
    typer.echo(f"{len(items)} open review items")


@review_app.command("label")
def review_label(
    story_id: str,
    category: str,
    reviewer: Annotated[str, typer.Option(help="Who labeled the story.")] = "owner",
    note: Annotated[str | None, typer.Option(help="Free-text reason.")] = None,
    config: ConfigDir = None,
    database_url: DatabaseUrl = None,
) -> None:
    """Label a story (human label; resolves its review item and publishes it)."""
    taxonomy = load_categories(config_dir(config) / "categories.toml")
    engine = _engine(database_url)
    with _db_errors():
        label_id = human_label(engine, taxonomy, story_id, category, reviewer, note)
    typer.echo(f"label {label_id}: {story_id} -> {category} by {reviewer}")


@review_app.command("simulate")
def review_simulate(
    data_dir: DataDir = Path("data"), config: ConfigDir = None, database_url: DatabaseUrl = None
) -> None:
    """Resolve open review items with the simulated reviewer (ground truth; demo only)."""
    taxonomy = load_categories(config_dir(config) / "categories.toml")
    engine = _engine(database_url)
    with _db_errors():
        n = simulate_reviews(engine, label_stories(data_dir), taxonomy)
    typer.echo(f"sim_reviewer labeled {n} stories")


@review_app.command("export")
def review_export(
    data_dir: DataDir = Path("data"),
    out: Annotated[
        Path | None, typer.Option(help="Output dir (default <data-dir>/labels).")
    ] = None,
    ground_truth: Annotated[
        bool, typer.Option(help="Add ground-truth labels for stories nobody reviewed.")
    ] = True,
    no_db: Annotated[bool, typer.Option("--no-db", help="Ground truth only, no Postgres.")] = False,
    config: ConfigDir = None,
    database_url: DatabaseUrl = None,
) -> None:
    """Write the labeled training set (JSONL + manifest with a content-hash version)."""
    household = load_household(config_dir(config) / "household.toml")
    engine = None if no_db else _engine(database_url)
    with _db_errors():
        rows = build_training_set(data_dir, household, engine, ground_truth)
    m = write_training_set(rows, out or data_dir / "labels")
    by_source = ", ".join(f"{k}={v}" for k, v in m.by_source.items())
    typer.echo(f"training set {m.version}: {m.examples} examples ({by_source})")


@review_app.command("case")
def review_case(
    title: str,
    story: Annotated[list[str] | None, typer.Option(help="Story id (repeatable).")] = None,
    created_by: Annotated[str, typer.Option(help="Case owner.")] = "owner",
    note: Annotated[str | None, typer.Option(help="Case note.")] = None,
    database_url: DatabaseUrl = None,
) -> None:
    """Open a change-control case for one or more stories."""
    engine = _engine(database_url)
    with _db_errors():
        case_id = create_case(engine, title, created_by, story or [], note)
    typer.echo(f"case {case_id}: {title}")


ModelsDir = Annotated[Path, typer.Option(help="Model directory (gitignored).")]


@app.command()
def train(
    training_set: Annotated[
        Path, typer.Option(help="training_set.jsonl from `d2d review export`.")
    ] = Path("data/labels/training_set.jsonl"),
    models_dir: ModelsDir = Path("models"),
    force: Annotated[bool, typer.Option(help="Retrain even if the model is up to date.")] = False,
) -> None:
    """Stage 7: train the classifier and choose per-class abstain thresholds."""
    rows = read_training_set(training_set)
    version = training_set_version(rows)
    current = load_meta(models_dir)
    if not force and current and current.model_version == model_version(version, TrainParams()):
        typer.echo(f"model {current.model_version} up to date (training set {version})")
        return
    model, meta = train_model(rows, version)
    save_model(model, meta, models_dir)
    typer.echo(
        f"model {meta.model_version}: {meta.examples} examples "
        f"({meta.augmented_examples} augmented), {len(meta.classes)} classes -> {models_dir}"
    )


@app.command()
def classify(
    models_dir: ModelsDir = Path("models"),
    config: ConfigDir = None,
    database_url: DatabaseUrl = None,
) -> None:
    """Label new stories in Postgres; abstained and audit-sampled stories go to review."""
    taxonomy = load_categories(config_dir(config) / "categories.toml")
    classifier = Classifier(models_dir)
    engine = _engine(database_url)
    with _db_errors():
        s = classify_new(engine, classifier, taxonomy)
    typer.echo(
        f"{s.classified} new stories: {s.published} published, {s.audit} audit, "
        f"{s.abstained} abstained ({s.model_version})"
    )


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address (keep 127.0.0.1).")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    data_dir: DataDir = Path("data"),
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes (dev).")] = False,
) -> None:
    """Run the API, agent and web UI (http://127.0.0.1:8000)."""
    import os

    import uvicorn

    os.environ.setdefault("D2D_DATA_DIR", str(data_dir.resolve()))
    uvicorn.run(
        "d2d.api.main:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        proxy_headers=False,
    )


EnvOpt = Annotated[
    str | None, typer.Option("--env", help="staging or production (~/d2d/<env>/users.toml).")
]
UsersFileOpt = Annotated[
    Path | None, typer.Option(help="Users file (default: --env, else $D2D_USERS_FILE).")
]


def _users(env: str | None, users_file: Path | None) -> UsersFile:
    try:
        return UsersFile(users_file_for(env, users_file))
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _read_password(password_stdin: bool) -> str:
    if password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(f"Password (min {MIN_PASSWORD} characters, not shown): ")
    if getpass.getpass("Repeat password: ") != first:
        typer.echo("passwords do not match", err=True)
        raise typer.Exit(code=1)
    return first


def _save_user(
    username: str,
    role: str,
    env: str | None,
    users_file: Path | None,
    password_stdin: bool,
    config: Path | None,
) -> None:
    users = _users(env, users_file)
    try:
        policy = load_account_policy(config_dir(config) / "roles.toml")
        existing = {u.username: u.role for u in users.users().values()}
        policy.check_new(existing, username, role)
    except ConfigError as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        users.set_user(username, role, _read_password(password_stdin), ROLES)
    except AuthError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"saved {username} ({role}) -> {users.path} (password hash only, mode 600)")


@user_app.command("add")
def user_add(
    username: str,
    role: Annotated[str, typer.Option(help="Allowed roles: config/roles.toml [accounts].")] = (
        "owner"
    ),
    env: EnvOpt = None,
    users_file: UsersFileOpt = None,
    password_stdin: Annotated[bool, typer.Option(help="Read the password from stdin.")] = False,
    config: ConfigDir = None,
) -> None:
    """Create (or replace) an account, within the [accounts] policy (default: one owner)."""
    _save_user(username, role, env, users_file, password_stdin, config)


@user_app.command("passwd")
def user_passwd(
    username: str,
    env: EnvOpt = None,
    users_file: UsersFileOpt = None,
    password_stdin: Annotated[bool, typer.Option(help="Read the password from stdin.")] = False,
    config: ConfigDir = None,
) -> None:
    """Change a password (signs that user out everywhere)."""
    existing = _users(env, users_file).get(username)
    if existing is None:
        typer.echo(f"error: no user {username!r}", err=True)
        raise typer.Exit(code=1)
    _save_user(username, existing.role, env, users_file, password_stdin, config)


@user_app.command("remove")
def user_remove(username: str, env: EnvOpt = None, users_file: UsersFileOpt = None) -> None:
    """Delete an account (signs it out everywhere)."""
    users = _users(env, users_file)
    try:
        users.remove(username)
    except AuthError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"removed {username} from {users.path}")


@user_app.command("list")
def user_list(env: EnvOpt = None, users_file: UsersFileOpt = None) -> None:
    """List accounts and roles (never prints hashes)."""
    users = _users(env, users_file)
    for u in sorted(users.users().values(), key=lambda u: u.username):
        typer.echo(f"{u.username:<20}{u.role:<16}created {u.created}")
    typer.echo(f"{len(users.users())} users in {users.path}")


def _ontology(cfg_dir: Path, data_dir: Path, engine: Engine) -> Ontology:
    return Ontology(
        engine,
        load_household(cfg_dir / "household.toml"),
        load_categories(cfg_dir / "categories.toml"),
        load_enrichment(cfg_dir / "enrichment.toml", cfg_dir / "semantics.toml"),
        data_dir,
    )


@app.command()
def notify(
    data_dir: DataDir = Path("data"), config: ConfigDir = None, database_url: DatabaseUrl = None
) -> None:
    """Stage 9 notifications: one per new published story; push via ntfy when configured."""
    cfg_dir = config_dir(config)
    engine = _engine(database_url)
    onto = _ontology(cfg_dir, data_dir, engine)
    owner = Scope(load_roles(cfg_dir / "roles.toml")["owner"], onto.household, onto.taxonomy)
    sender = sender_from_env()
    with _db_errors():
        s = run_notifications(onto, owner, load_notify(cfg_dir / "notifications.toml"), sender)
    channel = "ntfy" if sender else "no push channel (set D2D_NTFY_URL/D2D_NTFY_TOPIC)"
    typer.echo(
        f"notifications: {s.new} new, {s.sent} sent, {s.deferred} deferred (quiet hours), "
        f"{s.recorded} recorded, {s.failed} failed, {s.backfill} backfill [{channel}]"
    )


@feedback_app.command("route")
def feedback_route(database_url: DatabaseUrl = None) -> None:
    """Route new feedback to labels, eval sets, data reports or the UX inbox."""
    engine = _engine(database_url)
    with _db_errors():
        counts = route_feedback(engine)
    routed = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing new"
    typer.echo(f"feedback routed: {routed}")


@feedback_app.command("mark-exported")
def feedback_mark_exported(
    summary: Annotated[Path, typer.Option(help="summary.json written by `d2d calibrate`.")],
    database_url: DatabaseUrl = None,
) -> None:
    """Mark the eval items of a calibration run as exported (after its branch was pushed)."""
    ids = [int(i) for i in json.loads(summary.read_text("utf-8")).get("item_ids", [])]
    engine = _engine(database_url)
    with _db_errors():
        mark_exported(engine, ids)
    typer.echo(f"{len(ids)} eval items marked exported")


@app.command("calibrate")
def calibrate_cmd(
    out: Annotated[Path, typer.Option(help="Proposal directory (report.md, summary.json, ...).")],
    data_dir: DataDir = Path("data"),
    models_dir: ModelsDir = Path("models"),
    golden: Annotated[Path, typer.Option(help="Frozen golden stories.")] = Path(
        "evals/golden/stories.jsonl"
    ),
    no_db: Annotated[bool, typer.Option("--no-db", help="Skip feedback export.")] = False,
    config: ConfigDir = None,
    database_url: DatabaseUrl = None,
) -> None:
    """Nightly calibration: propose linking windows, compare a retrained model, export evals."""
    cfg_dir = config_dir(config)
    engine = None if no_db else _engine(database_url)
    home_id = load_household(cfg_dir / "household.toml").id
    with _db_errors():
        s = calibrate(data_dir, cfg_dir, models_dir, golden, home_id, out, engine)
    best, cur = s["linking"]["best"], s["linking"]["current"]
    typer.echo(
        f"linking F1 {cur['pair_f1']:.3f} -> {best['pair_f1']:.3f} "
        f"({'proposal' if s['linking']['changed'] else 'no change'}); "
        f"eval items {sum(s['eval_items'].values())}; report -> {out / 'report.md'}"
    )


@app.command("launchd-plist")
def launchd_plist(
    env: Annotated[str, typer.Option(help="Environment: staging or production.")],
    env_dir: Annotated[Path, typer.Option(help="Environment root, e.g. ~/d2d/production.")],
    service: Annotated[str, typer.Option(help="pipeline, api (always on) or backup (daily).")] = (
        "pipeline"
    ),
    config: ConfigDir = None,
) -> None:
    """Print a launchd agent: the scheduled pipeline (cadence_minutes) or the API server."""
    pipeline = load_pipeline(config_dir(config) / "pipeline.toml")
    try:
        if service == "api":
            xml = api_plist(env, env_dir)
        elif service == "backup":
            xml = backup_plist(env, env_dir)
        elif service == "pipeline":
            xml = pipeline_plist(env, env_dir, pipeline.cadence_minutes)
        else:
            raise ValueError(f"unknown service {service!r}, expected pipeline, api or backup")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(xml.decode("utf-8"), nl=False)


@eval_app.command("linking")
def eval_linking(
    data_dir: DataDir = Path("data"),
    min_f1: Annotated[float, typer.Option(help="Gate: pairwise F1.")] = 0.9,
    min_precision: Annotated[float, typer.Option(help="Gate: pairwise precision.")] = 1.0,
) -> None:
    """Score linking vs scenario ground truth; exit 1 if a gate fails."""
    r = evaluate_linking(data_dir)
    for name, value in r.items():
        typer.echo(f"{name:<15}{value:.3f}")
    ok = r["pair_f1"] >= min_f1 and r["pair_precision"] >= min_precision
    typer.echo("gate: PASS" if ok else "gate: FAIL")
    if not ok:
        raise typer.Exit(code=1)


@eval_app.command("classifier")
def eval_classifier(
    models_dir: ModelsDir = Path("models"),
    golden: Annotated[Path, typer.Option(help="Frozen golden stories.")] = Path(
        "evals/golden/stories.jsonl"
    ),
    min_precision: Annotated[float, typer.Option(help="Gate: per-class precision.")] = 0.9,
    min_coverage: Annotated[float, typer.Option(help="Gate: 1 - abstain rate.")] = 0.7,
) -> None:
    """Score the classifier on the golden set; exit 1 if a gate fails."""
    r = evaluate_classifier(Classifier(models_dir), read_training_set(golden))
    typer.echo(f"coverage      {r.coverage:.3f}  ({r.covered}/{r.examples})")
    typer.echo(f"min precision {r.min_precision:.3f}")
    for c, p in r.precision_by_class.items():
        typer.echo(f"  {c:<24}{p:.3f}")
    for sid, want, got in r.errors[:10]:
        typer.echo(f"  wrong {sid}: {want} -> {got}")
    ok = r.min_precision >= min_precision and r.coverage >= min_coverage
    typer.echo("gate: PASS" if ok else "gate: FAIL")
    if not ok:
        raise typer.Exit(code=1)


@eval_app.command("changelog")
def eval_changelog(
    data_dir: DataDir = Path("data"),
    min_recall: Annotated[float, typer.Option(help="Gate: event recall.")] = 1.0,
    min_precision: Annotated[float, typer.Option(help="Gate: event precision.")] = 1.0,
    min_dq_recall: Annotated[float, typer.Option(help="Gate: data-quality recall.")] = 0.95,
) -> None:
    """Score change events against scenario ground truth; exit 1 if a gate fails."""
    r = evaluate_changelog(data_dir)
    typer.echo(f"recall    {r.recall:.3f}  ({r.matched}/{r.expected})")
    typer.echo(f"precision {r.precision:.3f}  ({r.matched}/{r.found})")
    typer.echo(f"dq recall {r.dq_recall:.3f}  ({r.dq_matched}/{r.dq_expected})")
    for key in r.missed[:10]:
        typer.echo(f"  missed     {key}")
    for key in r.unexpected[:10]:
        typer.echo(f"  unexpected {key}")
    gates = [
        r.recall >= min_recall,
        r.precision >= min_precision,
        r.dq_recall >= min_dq_recall,
    ]
    typer.echo("gate: PASS" if all(gates) else "gate: FAIL")
    if not all(gates):
        raise typer.Exit(code=1)


def _voltage(cfg_dir: Path) -> float:
    return load_enrichment(cfg_dir / "enrichment.toml", cfg_dir / "semantics.toml").tariff.voltage


@security_app.command("scan")
def security_scan(
    data_dir: DataDir = Path("data"),
    config: ConfigDir = None,
    top: Annotated[int, typer.Option(help="Changes to print.")] = 10,
) -> None:
    """Score config/topology changes vs the approved baseline; write security/alerts.parquet."""
    cfg_dir = config_dir(config)
    try:
        events = read_events(data_dir)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    scored = score_events(
        events, load_household(cfg_dir / "household.toml"), voltage=_voltage(cfg_dir)
    )
    path = write_alerts(scored, data_dir)
    alerts = [s for s in scored if s.alert]
    typer.echo(f"{len(scored)} config/topology changes scored, {len(alerts)} alert(s) -> {path}")
    for s in scored[:top]:
        flag = "ALERT" if s.alert else "     "
        typer.echo(
            f"{flag} {s.score:.2f}  {s.device_id}.{s.property}: {s.old_value} -> {s.new_value}"
            f"  [{', '.join(s.reasons)}]"
        )


@eval_app.command("security")
def eval_security(
    data_dir: DataDir = Path("data"),
    config: ConfigDir = None,
    trials: Annotated[int, typer.Option(help="Red-team trials (one attack of each type).")] = 25,
    seed: Annotated[int, typer.Option(help="Seed for attack times and devices.")] = 7,
    min_precision_at_k: Annotated[float, typer.Option(help="Gate: precision@k.")] = 0.8,
    min_recall: Annotated[float, typer.Option(help="Gate: attack recall at threshold.")] = 0.8,
    max_false_alerts: Annotated[float, typer.Option(help="Gate: false alerts per trial.")] = 1.0,
) -> None:
    """Inject simulated attacks into the change stream; exit 1 if a detection gate fails."""
    cfg_dir = config_dir(config)
    household = load_household(cfg_dir / "household.toml")
    try:
        events = read_events(data_dir)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    r = evaluate_security(events, household, trials, seed, _voltage(cfg_dir))
    typer.echo(f"trials             {r.trials}  ({r.injected} injected attacks)")
    typer.echo(f"precision@k        {r.precision_at_k:.3f}")
    typer.echo(f"recall             {r.recall:.3f}")
    typer.echo(
        f"false alerts/trial {r.false_alerts_per_trial:.2f}  (clean stream: {r.clean_false_alerts})"
    )
    for kind, value in r.recall_by_attack.items():
        typer.echo(f"  {kind:<26}{value:.3f}")
    ok = (
        r.precision_at_k >= min_precision_at_k
        and r.recall >= min_recall
        and r.false_alerts_per_trial <= max_false_alerts
    )
    typer.echo("gate: PASS" if ok else "gate: FAIL")
    if not ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
