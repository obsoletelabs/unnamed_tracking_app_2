"""Adapt approved plugin actions to the host's existing Tasks scheduler."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.job_runs import bounded_delay, job_lease
from src.features.jobs import JobSpec, get_setting, record_run
from src.plugin_api.contracts import PluginScheduledTask, PluginUiDocument, UiAction, parse_semver
from src.plugin_api.grants import effective_capabilities, installation_is_executable
from src.plugin_api.runtime_client import (
    PluginRuntimeClient,
    PluginRuntimeRequestError,
    PluginRuntimeUnavailable,
)

logger = logging.getLogger(__name__)
client = PluginRuntimeClient()
_last_inventory: list[dict[str, Any]] = []
_running: dict[str, asyncio.Task[None]] = {}
_runtime_state = {"reachable": False}
# A person-initiated sync carries on through a plugin's checkpoints without waiting for the
# schedule, but never indefinitely: the rest resumes on the owner's autosync if it is enabled.
MAX_SYNC_STEPS = 25


def runtime_reachable() -> bool:
    """Whether the last inventory came from the live runtime rather than the cached copy."""
    return _runtime_state["reachable"]


def task_job_id(installation_id: UUID, task_id: str, user_id: UUID | None = None) -> str:
    """Keep schedules stable across updates without sharing another installation's rows."""
    if user_id is not None:
        return "user-plugin:" + str(uuid5(installation_id, f"{user_id}:{task_id}"))
    return "plugin:" + str(uuid5(installation_id, task_id))


async def _background_capabilities(
    db: AsyncSession,
    plugin: dict[str, Any],
    installation_id: UUID,
    target_user_id: UUID | None = None,
) -> frozenset[str]:
    """Use the schedule owner, or the approved identity for a server task."""
    try:
        user_id = target_user_id or UUID(str(plugin.get("background_user_id", "")))
    except ValueError:
        return frozenset()
    user = await db.get(User, user_id)
    if user is None or not user.is_active or (target_user_id is None and not user.is_admin):
        return frozenset()
    grants = await db.execute(
        select(PluginPermissionGrant.capability).where(
            PluginPermissionGrant.plugin_id == plugin["plugin_id"],
            PluginPermissionGrant.installation_id == installation_id,
            PluginPermissionGrant.capability_version == 1,
            PluginPermissionGrant.revoked_at.is_(None),
            PluginPermissionGrant.device_id.is_(None),
            or_(PluginPermissionGrant.user_id.is_(None), PluginPermissionGrant.user_id == user_id),
        )
    )
    return await effective_capabilities(
        db, plugin["plugin_id"], installation_id, user_id, list(grants.scalars())
    )


async def _task_availability(
    db: AsyncSession,
    plugin: dict[str, Any],
    installation_id: UUID,
    runtime_available: bool,
    user_id: UUID | None = None,
) -> tuple[frozenset[str], dict[str, UiAction], str, bool]:
    """Resolve lifecycle, exact-scope grants and declared actions before exposing controls.

    The last item says whether an unavailable task is permanently revoked (plugin disabled or
    background access withdrawn) rather than temporarily stopped or unreachable."""
    if not runtime_available:
        return frozenset(), {}, "Plugin runtime is unavailable. Scheduling is paused.", False
    if not installation_is_executable(plugin):
        return (
            frozenset(),
            {},
            "Start this compatible plugin to use its tasks.",
            plugin.get("enabled") is False,
        )
    capabilities = await _background_capabilities(db, plugin, installation_id, user_id)
    if "tasks.background" not in capabilities:
        return (
            capabilities,
            {},
            "Background permission or the task's account identity is unavailable.",
            True,
        )
    try:
        document = PluginUiDocument.model_validate(await client.plugin_ui(plugin["plugin_id"]))
    except (PluginRuntimeUnavailable, PluginRuntimeRequestError, ValidationError):
        return capabilities, {}, "Plugin task declarations are unavailable.", False
    return capabilities, {action.id: action for action in document.actions}, "", False


def _result_summary(result: dict[str, Any]) -> str:
    """Display only the bounded public task summary."""
    return str(result.get("summary") or "")[:512]


def _action_unavailable_reason(action: UiAction | None, capabilities: frozenset[str]) -> str:
    """Keep missing action and revoked permission diagnostics explicit."""
    if action is None or action.handler is None or action.confirmation is not None:
        return "This task must reference a declared action with no confirmation."
    if action.capability is not None and action.capability.name.value not in capabilities:
        return f"Required permission {action.capability.name.value} is unavailable."
    return ""


async def _dispatch(
    spec: JobSpec, installation_id: UUID, task_id: str, mode: str
) -> dict[str, Any]:
    """Recheck identity, grants, the owner's choice and pause state, then run one bounded step."""
    result: dict[str, Any] = {
        "completed": False,
        "summary": "Plugin task could not run. Check its diagnostics.",
    }
    async with SessionLocal() as db:
        current = next(
            (job for job in await get_plugin_jobs(db, spec.user_id) if job.id == spec.id), None
        )
        setting = await get_setting(db, current or spec)
        if current is None or not current.available:
            result["summary"] = (
                current.unavailable_reason
                if current
                else "This plugin task is no longer installed."
            )
        elif mode == "needed" and spec.user_id is not None and not setting.enabled:
            result["summary"] = "Autosync was disabled before this step started."
        elif mode == "needed" and (setting.paused or setting.cancel_requested):
            result["summary"] = "This task is paused. Resume it to continue from the saved step."
        else:
            response = await client.run_scheduled_task(
                str(spec.plugin_id),
                str(installation_id),
                task_id,
                "scheduled" if mode == "needed" else "manual",
                **({"user_id": str(spec.user_id)} if spec.user_id is not None else {}),
            )
            completed = response.get("completed") is not False and response.get("ok") is not False
            summary = response.get("summary")
            result = {
                "completed": completed,
                "summary": summary[:512]
                if isinstance(summary, str)
                else "Completed"
                if completed
                else "Task reported a failure.",
            }
            if spec.user_id is not None and not completed:
                for field in ("continue_after_seconds", "retry_after_seconds"):
                    delay = bounded_delay(response.get(field))
                    if delay is not None:
                        result[field] = delay
                if response.get("disable_schedule") is True:
                    result["disable_schedule"] = True
    return result


async def _continues(spec: JobSpec, result: dict[str, Any], mode: str) -> bool:
    """Whether a "complete" run should immediately take the next checkpoint step.

    Only a saved, unfinished step that asked to continue qualifies; a provider rate limit, a
    disabled schedule, a pause or a cancel request hands control back to the normal controls."""
    if (
        mode != "complete"
        or result.get("completed") is not False
        or "continue_after_seconds" not in result
        or "retry_after_seconds" in result
        or result.get("disable_schedule") is True
    ):
        return False
    async with SessionLocal() as db:
        setting = await get_setting(db, spec)
        stopped = setting.paused or setting.cancel_requested
        await db.commit()
    return not stopped


async def _execute_task(spec: JobSpec, installation_id: UUID, task_id: str, mode: str) -> None:
    """Hold the job's durable lease for one step so no second run can overlap it."""
    try:
        async with job_lease(spec) as acquired:
            if not acquired:
                return
            result: dict[str, Any] = {
                "completed": False,
                "summary": "Plugin task could not run. Check its diagnostics.",
            }
            try:
                result = await _dispatch(spec, installation_id, task_id, mode)
                steps = 1
                while steps < MAX_SYNC_STEPS and await _continues(spec, result, mode):
                    result = await _dispatch(spec, installation_id, task_id, mode)
                    steps += 1
            except (PluginRuntimeUnavailable, PluginRuntimeRequestError):
                logger.warning("Plugin task dispatch failed: %s", spec.id)
            except (SQLAlchemyError, OSError, ValueError):
                logger.exception("Plugin task failed: %s", spec.id)
            try:
                await record_run(spec.id, result, spec=spec)
            except SQLAlchemyError:
                logger.exception("Plugin task result could not be saved: %s", spec.id)
    except SQLAlchemyError:
        logger.exception("Plugin task could not claim its run: %s", spec.id)
    finally:
        _running.pop(spec.id, None)


def _job_spec(
    plugin: dict[str, Any],
    installation_id: UUID,
    declaration: PluginScheduledTask,
    reason: str,
    *,
    user_id: UUID | None = None,
    revoked: bool = False,
) -> JobSpec:
    """Reuse ordinary schedule persistence, Run now and no-overlap behavior."""
    job_id = task_job_id(installation_id, declaration.id, user_id)

    def start(mode: str) -> dict[str, Any]:
        if reason:
            return {"running": False, "detail": reason}
        if job_id not in _running:
            _running[job_id] = asyncio.create_task(
                _execute_task(spec, installation_id, declaration.id, mode)
            )
        return {"running": True}

    spec = JobSpec(
        id=job_id,
        name=declaration.name,
        description=declaration.description,
        min_interval_minutes=declaration.min_interval_minutes,
        max_interval_minutes=declaration.max_interval_minutes,
        default_interval_minutes=declaration.default_interval_minutes,
        default_enabled=False,
        start=start,
        is_running=lambda: job_id in _running,
        summarize=_result_summary,
        manual_mode="all",
        plugin_id=str(plugin["plugin_id"]),
        plugin_name=str(plugin.get("name") or plugin["plugin_id"]),
        available=not reason,
        unavailable_reason=reason,
        user_id=user_id,
        revoked=revoked,
        retry_backoff=True,
    )
    return spec


def _declared_tasks(
    plugin: dict[str, Any],
) -> tuple[UUID, list[PluginScheduledTask], tuple[int, int, int]] | None:
    """Parse one installation's bounded, unique task declarations, or skip it."""
    raw_tasks = plugin.get("scheduled_tasks")
    if (
        plugin.get("legacy_compatibility")
        or not isinstance(raw_tasks, list)
        or not raw_tasks
        or len(raw_tasks) > 32
    ):
        return None
    try:
        installation_id = UUID(str(plugin.get("installation_id", "")))
        declarations = [PluginScheduledTask.model_validate(task) for task in raw_tasks]
        api_version = parse_semver(plugin.get("api_contract_version", "0.0.0"))
    except (ValueError, ValidationError):
        return None
    if len({task.id for task in declarations}) != len(declarations):
        return None
    return installation_id, declarations, api_version


def _declaration_job(
    plugin: dict[str, Any],
    installation_id: UUID,
    declaration: PluginScheduledTask,
    availability: tuple[frozenset[str], dict[str, UiAction], str, bool],
    user_id: UUID | None,
) -> JobSpec:
    """Combine installation-wide and per-action availability into one job."""
    capabilities, actions, shared_reason, shared_revoked = availability
    action_reason = _action_unavailable_reason(actions.get(declaration.action_id), capabilities)
    return _job_spec(
        plugin,
        installation_id,
        declaration,
        shared_reason or action_reason,
        user_id=user_id,
        revoked=shared_revoked or (bool(action_reason) and not shared_reason),
    )


async def _plugin_task_jobs(
    db: AsyncSession,
    plugin: dict[str, Any],
    declared: tuple[UUID, list[PluginScheduledTask], tuple[int, int, int]],
    runtime_available: bool,
    user_id: UUID | None,
) -> list[JobSpec]:
    """Build the jobs one installation contributes for the server or one account."""
    installation_id, declarations, api_version = declared
    availability = await _task_availability(db, plugin, installation_id, runtime_available, user_id)
    return [
        _declaration_job(plugin, installation_id, declaration, availability, user_id)
        for declaration in declarations
        if (declaration.scope == "user") == (user_id is not None)
        and not (declaration.scope == "user" and api_version < (1, 1, 11))
    ]


async def get_plugin_jobs(db: AsyncSession, user_id: UUID | None = None) -> list[JobSpec]:
    """Return bounded v1.1 jobs; cached declarations stay visibly paused during outages."""
    try:
        inventory = await client.plugins()
        _last_inventory[:] = inventory
        runtime_available = True
    except (PluginRuntimeUnavailable, PluginRuntimeRequestError):
        inventory = _last_inventory
        runtime_available = False
    _runtime_state["reachable"] = runtime_available
    jobs: list[JobSpec] = []
    for plugin in inventory:
        declared = _declared_tasks(plugin)
        if declared is None:
            continue
        jobs.extend(await _plugin_task_jobs(db, plugin, declared, runtime_available, user_id))
    return jobs


async def personal_sync_job(
    db: AsyncSession, plugin: dict[str, Any], user_id: UUID, action_id: str
) -> JobSpec | None:
    """The account's personal task that runs a connection's declared sync action, if any.

    It is the same job the Tasks page lists, so its grants, pause state and no-overlap lease
    apply to a sync started from the Connections page."""
    declared = _declared_tasks(plugin)
    if declared is None:
        return None
    installation_id, declarations, api_version = declared
    task = next(
        (item for item in declarations if item.scope == "user" and item.action_id == action_id),
        None,
    )
    if task is None or api_version < (1, 1, 11):
        return None
    availability = await _task_availability(db, plugin, installation_id, True, user_id)
    return _declaration_job(plugin, installation_id, task, availability, user_id)
