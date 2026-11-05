"""`flagman-load`: one command for a whole run, see docs/09-load-test.md."""

import argparse
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pyotp

from flagman_load import pools, stack
from flagman_load.bootstrap import (
    BootstrapError,
    Target,
    prepare_clients,
    prepare_renters,
    read_zones,
    refuse_real_clients,
    staff_login,
)
from flagman_load.profiles import LOCAL_CAR_COUNTS, PILOT, SMOKE, STAND, Profile
from flagman_load.run_state import STATE_ENV, RunState
from flagman_load.thresholds import Check, RunResult, evaluate

PROJECT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_DIR / "reports"
STAND_ENV = PROJECT_DIR / "stand.env"
LOCUSTFILE = Path(__file__).with_name("locustfile.py")
_LOCUST_GRACE_S = 120


@dataclass(frozen=True)
class StaffAccount:
    email: str
    password: str
    totp_secret: str


@dataclass(frozen=True)
class Plan:
    profile: Profile
    target: Target
    ws_url: str
    origin: str
    phones: Sequence[str]
    otp_code: str
    staff: Sequence[StaffAccount]


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


def stand_plan(values: Mapping[str, str]) -> Plan:
    missing = [
        key
        for key in (
            "LOAD_STAND_API_URL",
            "LOAD_STAND_ADMIN_ORIGIN",
            "LOAD_STAND_PHONES",
            "LOAD_STAND_OTP_CODE",
            "LOAD_STAND_STAFF_EMAIL",
            "LOAD_STAND_STAFF_PASSWORD",
            "LOAD_STAND_STAFF_TOTP_SECRET",
        )
        if not values.get(key)
    ]
    if missing:
        raise BootstrapError(f"set in tests/load/stand.env: {', '.join(missing)}")
    base_url = values["LOAD_STAND_API_URL"].rstrip("/")
    if urlsplit(base_url).scheme != "https":
        raise BootstrapError("LOAD_STAND_API_URL must be https")
    phones = [phone.strip() for phone in values["LOAD_STAND_PHONES"].split(",") if phone.strip()]
    if len(phones) < STAND.phones_needed:
        raise BootstrapError(
            f"LOAD_STAND_PHONES lists {len(phones)} phones, the run needs {STAND.phones_needed}"
        )
    return Plan(
        profile=STAND,
        target=Target(base_url, send_client_ip=False),
        ws_url=f"wss://{urlsplit(base_url).netloc}/admin/fleet/stream",
        origin=values["LOAD_STAND_ADMIN_ORIGIN"],
        phones=phones,
        otp_code=values["LOAD_STAND_OTP_CODE"],
        staff=[
            StaffAccount(
                values["LOAD_STAND_STAFF_EMAIL"],
                values["LOAD_STAND_STAFF_PASSWORD"],
                values["LOAD_STAND_STAFF_TOTP_SECRET"],
            )
        ],
    )


def _prepare(plan: Plan, report_dir: Path) -> RunState:
    profile = plan.profile
    renter_end = profile.renter_pool
    map_end = renter_end + profile.map_users
    print(f"Signing in {map_end} clients...", flush=True)
    sessions = prepare_clients(plan.target, plan.phones[:map_end], plan.otp_code)
    refuse_real_clients(plan.target, sessions)
    renters, map_clients = sessions[:renter_end], sessions[renter_end:]

    staff = [
        staff_login(plan.target, account.email, account.password, account.totp_secret)
        for account in plan.staff
    ]
    print(f"Preparing {len(renters)} renters: documents, approval, card...", flush=True)
    prepare_renters(plan.target, renters, staff[0].access_token)
    return RunState(
        profile=profile.name,
        base_url=plan.target.base_url,
        ws_url=plan.ws_url,
        origin=plan.origin,
        send_client_ip=plan.target.send_client_ip,
        otp_code=plan.otp_code,
        renters=renters,
        map_clients=map_clients,
        otp_phones=list(plan.phones[map_end : map_end + profile.otp_phones]),
        staff=staff,
        zones=read_zones(plan.target, sessions[0]),
        summary_path=str(report_dir / "summary.json"),
    )


def _run_locust(state: RunState, profile: Profile, report_dir: Path) -> None:
    state_path = report_dir / "state.json"
    state.save(state_path)
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "locust",
                "-f",
                str(LOCUSTFILE),
                "--headless",
                "--users",
                str(profile.users),
                "--spawn-rate",
                str(profile.spawn_rate),
                "--run-time",
                f"{profile.run_time_s}s",
                "--stop-timeout",
                str(profile.stop_timeout_s),
                "--csv",
                str(report_dir / "locust"),
                "--logfile",
                str(report_dir / "locust.log"),
                "--only-summary",
                # The verdict is ours: targets, not "any request failed".
                "--exit-code-on-error",
                "0",
            ],
            env={**os.environ, STATE_ENV: str(state_path)},
            check=True,
            timeout=profile.run_time_s + profile.stop_timeout_s + _LOCUST_GRACE_S,
        )
    finally:
        # Holds live tokens of the stand or of the local stack.
        state_path.unlink(missing_ok=True)


def _judge(profile: Profile, report_dir: Path) -> list[Check]:
    result = RunResult.load(report_dir / "summary.json")
    return evaluate(profile.targets, result.requests, result.summary, result.crashes)


def _report(checks: Sequence[Check], report_dir: Path) -> bool:
    width = max(len(check.name) for check in checks)
    for check in checks:
        verdict = "ok  " if check.ok else "MISS"
        print(f"{verdict} {check.name:<{width}}  {check.target:<18} {check.actual}")
    (report_dir / "result.json").write_text(
        json.dumps([vars(check) for check in checks], indent=2), encoding="utf-8"
    )
    passed = all(check.ok for check in checks)
    print(f"\n{'All targets met' if passed else 'Targets missed'}. Details: {report_dir}")
    return passed


def _new_report_dir(profile: Profile) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report_dir = REPORTS_DIR / f"{stamp}-{profile.name}"
    report_dir.mkdir(parents=True)
    return report_dir


def _execute(plan: Plan, report_dir: Path) -> bool:
    state = _prepare(plan, report_dir)
    print(
        f"Running {plan.profile.name}: {plan.profile.users} users, {plan.profile.run_time_s} s",
        flush=True,
    )
    _run_locust(state, plan.profile, report_dir)
    return _report(_judge(plan.profile, report_dir), report_dir)


def run_local(profile: Profile, keep: bool) -> bool:
    report_dir = _new_report_dir(profile)
    otp_code = pools.new_otp_code()
    phones = pools.phones(profile.phones_needed)
    env_file = report_dir / "stack.env"
    local = stack.Stack(env_file)
    local.write_env(stack.local_env(LOCAL_CAR_COUNTS[profile.name], phones, otp_code))
    staff = [
        StaffAccount(f"load{i}@flagman.example", secrets.token_urlsafe(24), pyotp.random_base32())
        for i in range(profile.staff_streams)
    ]
    try:
        local.up()
        for account in staff:
            local.ensure_operator(account.email, account.password, account.totp_secret)
        return _execute(
            Plan(
                profile=profile,
                target=Target(stack.BASE_URL, send_client_ip=True),
                ws_url=stack.WS_URL,
                origin=stack.ORIGIN,
                phones=phones,
                otp_code=otp_code,
                staff=staff,
            ),
            report_dir,
        )
    finally:
        local.save_logs(report_dir / "stack.log")
        if not keep:
            local.down()
            # Only the secrets of the stack that has just been removed.
            env_file.unlink(missing_ok=True)


def run_stand() -> bool:
    if not STAND_ENV.exists():
        raise BootstrapError(
            "copy tests/load/stand.env.example to tests/load/stand.env, fill it in"
        )
    plan = stand_plan(parse_env(STAND_ENV.read_text(encoding="utf-8")))
    return _execute(plan, _new_report_dir(plan.profile))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flagman-load", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("local", help="run against a throwaway local Compose stack")
    local.add_argument("profile", choices=[SMOKE.name, PILOT.name])
    local.add_argument("--keep", action="store_true", help="leave the stack up after the run")
    commands.add_parser("stand", help="run the small profile against the demo stand")
    args = parser.parse_args(argv)
    try:
        if args.command == "stand":
            passed = run_stand()
        else:
            passed = run_local(SMOKE if args.profile == SMOKE.name else PILOT, args.keep)
    # Whatever it was: Python's own exit code for it is 1, and here that means "targets missed".
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0 if passed else 1
