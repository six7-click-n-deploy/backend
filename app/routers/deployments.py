import asyncio
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.crud import app_version_approvals as crud_approvals
from app.crud import apps as crud_apps
from app.crud import deployments as crud_deployments
from app.crud import locks as crud_locks
from app.crud import openstack_credentials as crud_openstack_credentials
from app.crud import teams as crud_teams
from app.database import get_db
from app.models import TaskStatus, TaskType, User, UserRole
from app.schemas import (
    DeploymentCreate,
    DeploymentDetail,
    DeploymentOutputs,
    DeploymentResponse,
    DeploymentTeamMember,
    DeploymentTeamResponse,
    TaskSummary,
)
from app.services import deployment_notifier
from app.services import task_service as task_service_module
from app.services.deployment_pubsub import pubsub
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import (
    ensure_deployment_access,
    ensure_deployment_owner_view,
    is_deployment_owner_view,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ----------------------------------------------------------------
# GET ALL DEPLOYMENTS
# ----------------------------------------------------------------
@router.get("/", response_model=list[DeploymentResponse])
def list_deployments(
    skip: int = 0,
    limit: int = 100,
    app_id: UUID | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """List deployments visible to the caller.

    Visibility rules:
      * **Teachers/admins** see deployments they created (their own
        owned set). Cross-user listing is intentional UX: a teacher
        opens an individual deployment via direct link or via a
        student's profile page, not from this index.
      * **Students** see deployments they own *or* are a member of —
        either via a team mapping or a direct ``UserToDeployment``
        row. So a student picked into a team for a deploy by their
        teacher finds it here without the teacher having to share a
        link.
    """
    if current_user.role in (UserRole.TEACHER, UserRole.ADMIN):
        deployments = crud_deployments.get_deployments(
            db,
            skip=skip,
            limit=limit,
            user_id=current_user.userId,
            app_id=app_id,
            status=status_filter,
        )
    else:
        deployments = crud_deployments.get_deployments(
            db,
            skip=skip,
            limit=limit,
            member_user_id=current_user.userId,
            app_id=app_id,
            status=status_filter,
        )

    # Enrich with status and created_at from tasks
    result = []
    for deployment in deployments:
        status_value = crud_deployments.get_deployment_status(db, deployment.deploymentId)
        created_at = crud_deployments.get_deployment_created_at(db, deployment.deploymentId)
        # Parse userInputVar JSON string back to dict if it exists
        user_input_var_parsed = None
        if deployment.userInputVar:
            try:
                user_input_var_parsed = json.loads(deployment.userInputVar)
            except json.JSONDecodeError:
                user_input_var_parsed = None

        result.append(DeploymentResponse(
            deploymentId=deployment.deploymentId,
            name=deployment.name,
            appId=deployment.appId,
            userId=deployment.userId,
            releaseTag=deployment.releaseTag,
            userInputVar=user_input_var_parsed,
            status=status_value,
            created_at=created_at,
        ))

    return result


# ----------------------------------------------------------------
# GET DEPLOYMENT BY ID (Full Details)
# ----------------------------------------------------------------
@router.get("/{deployment_id}", response_model=DeploymentDetail)
def get_deployment(
    deployment_id: UUID,
    include_logs: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get deployment by ID with full details including:
    - User and App relations
    - Teams with members
    - Latest task status
    - Terraform outputs
    - Optionally: full logs (use include_logs=true)
    """
    deployment = crud_deployments.get_deployment_with_details(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )

    # Check access permission
    ensure_deployment_access(deployment, current_user, db)

    # Get latest task
    latest_task = crud_deployments.get_latest_task(db, deployment_id)
    task_summary = None
    logs = None

    if latest_task:
        task_summary = TaskSummary(
            taskId=latest_task.taskId,
            type=latest_task.type,
            status=latest_task.status,
            started_at=latest_task.started_at,
            finished_at=latest_task.finished_at,
            created_at=latest_task.created_at,
            current_phase=getattr(latest_task, "current_phase", None),
            progress_pct=getattr(latest_task, "progress_pct", None),
        )
        if include_logs:
            logs = latest_task.logs

    # Get teams with members. The owner view sees every team and
    # every member. The member view only sees their own team(s) so
    # they can't browse who else has access to the deployment.
    is_owner_view = is_deployment_owner_view(deployment, current_user)
    teams_data = crud_deployments.get_deployment_teams_with_members(db, deployment_id)
    if not is_owner_view:
        teams_data = [
            t for t in teams_data
            if any(str(m["userId"]) == str(current_user.userId) for m in t["members"])
        ]
    teams = [
        DeploymentTeamResponse(
            teamId=team["teamId"],
            name=team["name"],
            members=[
                DeploymentTeamMember(
                    userId=member["userId"],
                    email=member["email"],
                    username=member["username"]
                )
                for member in team["members"]
            ]
        )
        for team in teams_data
    ]

    # Outputs / state / logs are owner-only — members don't get to
    # browse the credentials of teammates or the raw infrastructure
    # state. They have their own resend-access action for their own
    # credentials.
    if is_owner_view:
        outputs_data = crud_deployments.get_deployment_outputs(db, deployment_id)
        outputs = DeploymentOutputs(raw=outputs_data) if outputs_data else None
    else:
        outputs = None
        logs = None

    # Get status and created_at from tasks
    status_value = crud_deployments.get_deployment_status(db, deployment_id)
    created_at = crud_deployments.get_deployment_created_at(db, deployment_id)

    # Parse userInputVar JSON string back to dict if it exists
    user_input_var_parsed = None
    if deployment.userInputVar:
        try:
            user_input_var_parsed = json.loads(deployment.userInputVar)
        except json.JSONDecodeError:
            user_input_var_parsed = None

    return DeploymentDetail(
        deploymentId=deployment.deploymentId,
        name=deployment.name,
        appId=deployment.appId,
        userId=deployment.userId,
        releaseTag=deployment.releaseTag,
        userInputVar=user_input_var_parsed,
        status=status_value,
        created_at=created_at,
        user=deployment.user,
        app=deployment.app,
        teams=teams,
        latest_task=task_summary,
        outputs=outputs,
        logs=logs,
    )


# ----------------------------------------------------------------
# CREATE DEPLOYMENT
# ----------------------------------------------------------------
@router.post("/", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
def create_deployment(
    deployment: DeploymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Create a new deployment

    Atomicity: a per-user advisory lock serializes credential mutation
    with deployment dispatch. The deployment row, teams, user mappings,
    and the initial PENDING task row are all inserted in a single
    transaction, so the user can never end up with a deployment row
    that has no matching task. Celery dispatch happens AFTER commit;
    if it fails, the task row is flipped to FAILED so the deployment
    surfaces an honest error instead of hanging in PENDING forever.
    """
    # Per-user lock — serializes against PUT /me/openstack-credentials
    # and any other concurrent POST /deployments from this user. Held
    # until the next COMMIT/ROLLBACK on this connection.
    crud_locks.acquire_user_xact_lock(db, current_user.userId)

    # Refuse the create if the target app is soft-deleted.
    target_app = crud_apps.get_app(db, deployment.appId)
    if target_app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "app_not_found_or_deleted"},
        )

    # Enforce the release workflow:
    # - Private apps: only the creator may deploy (all versions allowed).
    # - Public apps: the requested releaseTag must have an APPROVED entry.
    if target_app.is_private:
        if str(target_app.userId) != str(current_user.userId):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"reason": "app_is_private"},
            )
    else:
        release_tag = deployment.releaseTag
        if not release_tag or not crud_approvals.has_approved_version(
            db, target_app.appId, release_tag
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"reason": "version_not_approved"},
            )

    db_deployment = crud_deployments.create_deployment(
        db, deployment, current_user.userId
    )

    user_ids_in_deployment = set()
    if deployment.teams:
        teams_data = [
            {"name": team.name, "userIds": team.userIds}
            for team in deployment.teams
        ]
        crud_teams.create_teams_for_deployment(
            db=db,
            deployment_id=db_deployment.deploymentId,
            teams_data=teams_data,
        )
        for team in deployment.teams:
            user_ids_in_deployment.update(team.userIds)

    if user_ids_in_deployment:
        crud_deployments.create_user_to_deployments(
            db=db,
            deployment_id=db_deployment.deploymentId,
            user_ids=user_ids_in_deployment,
        )

    # Parse user input variables
    try:
        user_vars = (
            json.loads(db_deployment.userInputVar) if db_deployment.userInputVar else {}
        )
    except Exception:
        user_vars = {}

    # Format teams for Terraform (team_name: [user_emails])
    teams_dict = {}
    if deployment.teams:
        from app.crud import users as crud_users
        for team in deployment.teams:
            team_users = []
            for user_id in team.userIds:
                user = crud_users.get_user(db, user_id)
                if user:
                    team_users.append({"email": user.email})
            teams_dict[team.name] = team_users

    # Per-user OpenStack credentials are required to deploy. The envelope
    # carries ciphertext only — the worker decrypts in-process. Reading
    # this inside the locked TX guarantees the envelope matches whatever
    # credential row a concurrent PUT might have committed: PUT is
    # serialized behind us by the same advisory lock.
    try:
        openstack_envelope = crud_openstack_credentials.get_dispatch_envelope(
            db, current_user.userId
        )
    except crud_openstack_credentials.NoCredentialError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"reason": "openstack_credentials_missing"},
        )

    # Insert PENDING task row in the SAME transaction as the deployment.
    # If anything below fails before commit, the rollback drops both —
    # no orphan rows.
    try:
        task = task_service_module.prepare_task_in_tx(
            db,
            deployment_id=db_deployment.deploymentId,
            task_type=TaskType.DEPLOY,
        )
    except task_service_module.ActiveTaskExistsError:
        # Should be impossible on a freshly inserted deployment, but
        # the partial unique index would catch it too.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment already has an active task",
        )

    # Atomic commit: deployment + teams + user_to_deployments + task row.
    # The advisory lock is released here.
    db.commit()
    db.refresh(db_deployment)
    db.refresh(task)

    # Dispatch to Celery OUTSIDE the locked TX. On failure the task row
    # is flipped to FAILED in a fresh TX (handled in dispatch_to_celery)
    # and we surface 503 — the deployment row stays, but the user sees
    # an obvious failure instead of an eternal PENDING.
    try:
        task, _celery_id = task_service_module.dispatch_to_celery(
            db,
            task=task,
            celery_task_name="tasks.deploy_application",
            celery_args=[
                str(db_deployment.deploymentId),
                str(db_deployment.appId),
                db_deployment.app.git_link,
                db_deployment.releaseTag,
                user_vars,
                teams_dict,
                openstack_envelope,
            ],
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not dispatch deployment task — please retry",
        )

    status_value = crud_deployments.get_deployment_status(db, db_deployment.deploymentId)
    created_at = crud_deployments.get_deployment_created_at(db, db_deployment.deploymentId)

    user_input_var_parsed = None
    if db_deployment.userInputVar:
        try:
            user_input_var_parsed = json.loads(db_deployment.userInputVar)
        except json.JSONDecodeError:
            user_input_var_parsed = None

    return DeploymentResponse(
        deploymentId=db_deployment.deploymentId,
        name=db_deployment.name,
        appId=db_deployment.appId,
        userId=db_deployment.userId,
        releaseTag=db_deployment.releaseTag,
        userInputVar=user_input_var_parsed,
        status=status_value,
        created_at=created_at,
    )


# ----------------------------------------------------------------
# DELETE DEPLOYMENT
# ----------------------------------------------------------------
#
# One endpoint, two outcomes — the backend picks the right one from
# the deployment's status:
#
#   * ``success`` / ``failed``  → dispatch a Destroy task (terraform
#     destroy + auto-soft-delete on success).  Returns 202 + task_id;
#     the frontend keeps the live stream open and routes back to the
#     list when the task finishes.
#   * ``cancelled``             → soft-delete immediately. Returns 204.
#   * any other status (running / pending / destroying)
#                               → 409, the user has to wait.
#
# Frontend doesn't have to know the difference — it just calls DELETE
# and switches into the live-stream view when the response is 202.
@router.delete("/{deployment_id}")
def delete_deployment(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """Unified delete — destroys OpenStack resources first if needed.

    Restricted to the owner-view (creator, teacher, admin). Members
    can read-access the deployment but never tear it down.
    """
    deployment = crud_deployments.get_deployment_with_details(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found",
        )

    ensure_deployment_access(deployment, current_user, db)
    ensure_deployment_owner_view(deployment, current_user)

    current_status = crud_deployments.get_deployment_status(db, deployment_id)

    # Active task in flight — neither path is safe.
    if current_status in ("pending", "running", "destroying"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete a deployment in status '{current_status}'. "
                "Wait for the active task to finish."
            ),
        )

    # Resources may exist — destroy them first; the listener will
    # auto-soft-delete the row when the destroy task succeeds.
    if current_status in ("success", "failed"):
        return _dispatch_destroy(db, deployment, current_user)

    # No resources to clean up (cancelled, or anything else terminal):
    # straight soft-delete.
    success = crud_deployments.soft_delete_deployment(db, deployment_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _dispatch_destroy(db: Session, deployment, current_user: User):
    """Enqueue the destroy worker task for a deployment.

    Extracted so DELETE can call it for the success/failed case and so
    the implementation stays close to the create-deployment dispatch
    pattern: atomic PENDING task insert → commit → ``send_task``.
    """
    try:
        user_vars = json.loads(deployment.userInputVar) if deployment.userInputVar else {}
    except Exception:
        user_vars = {}

    teams_dict: dict = {}
    if deployment.teams:
        # Persisted Team rows expose membership via the ``user_to_teams``
        # association, not a flat ``userIds`` field — that lives on the
        # request-side Pydantic schema in the create endpoint, not on
        # the ORM. ``get_team_members`` does the join for us.
        for team in deployment.teams:
            members = crud_deployments.get_team_members(db, team.teamId)
            teams_dict[team.name] = [{"email": m.email} for m in members]

    try:
        openstack_envelope = crud_openstack_credentials.get_dispatch_envelope(
            db, current_user.userId
        )
    except crud_openstack_credentials.NoCredentialError:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"reason": "openstack_credentials_missing"},
        )

    try:
        task = task_service_module.prepare_task_in_tx(
            db,
            deployment_id=deployment.deploymentId,
            task_type=TaskType.DESTROY,
        )
    except task_service_module.ActiveTaskExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment already has an active task",
        )

    db.commit()
    db.refresh(task)

    try:
        task, _celery_id = task_service_module.dispatch_to_celery(
            db,
            task=task,
            celery_task_name="tasks.destroy_deployment",
            celery_args=[
                str(deployment.deploymentId),
                str(deployment.appId),
                deployment.app.git_link,
                deployment.releaseTag,
                user_vars,
                teams_dict,
                openstack_envelope,
            ],
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not dispatch destroy task — please retry",
        )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"task_id": str(task.taskId), "status": "destroying"},
    )


# ----------------------------------------------------------------
# RESEND ACCESS MAIL FOR ONE TEAM MEMBER
# ----------------------------------------------------------------
@router.post(
    "/{deployment_id}/teams/{team_id}/users/{user_id}/resend-access",
    status_code=status.HTTP_202_ACCEPTED,
)
def resend_access_credentials(
    deployment_id: UUID,
    team_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """Re-send the per-user access mail for one team member.

    Reuses the original notify pipeline — same template, same
    credential extraction from the latest successful DEPLOY task's
    ``terraform_outputs``. Useful when the user lost their first mail
    or the deploy ran before the user's email got fixed.

    Access control: caller must have access to the deployment (owner
    or teacher/admin). The endpoint is intentionally idempotent —
    each call sends one mail to the targeted user. There's no rate
    limit at the API level; SMTP and Gmail's per-account quota are
    the natural backstops.

    Mapping ResendError to HTTP:
      * ``deployment_not_found`` → 404
      * ``team_not_in_deployment`` / ``user_not_in_team`` → 404
      * ``no_successful_deploy`` → 409 (nothing to resend yet)
      * ``no_credentials_for_user`` → 409 (template didn't issue
        per-user creds, or matcher missed despite the fuzzy logic)
    """
    deployment = crud_deployments.get_deployment(db, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found",
        )
    ensure_deployment_access(deployment, current_user, db)

    # Members may only re-send their own access mail. Owner-view
    # callers (creator, teacher, admin) can resend for anyone in
    # any team. Without this check a student in team A could trigger
    # a mail to anyone else's address, which is both privacy-leaky
    # and a tiny SMTP-amplification vector.
    if not is_deployment_owner_view(deployment, current_user) and str(user_id) != str(current_user.userId):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Members may only resend their own access mail",
        )

    try:
        sent = deployment_notifier.resend_user_access(
            db, deployment_id, team_id, user_id,
        )
    except deployment_notifier.ResendError as e:
        reason = str(e)
        # 404 for "this user/team isn't in this deployment", 409 for
        # "the deployment hasn't reached the state where it could
        # have emitted credentials yet".
        if reason in ("deployment_not_found", "team_not_in_deployment", "user_not_in_team"):
            http_status = status.HTTP_404_NOT_FOUND
        else:
            http_status = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=http_status, detail={"reason": reason})

    if not sent:
        # Template render + payload were fine, only SMTP rejected.
        # 502 makes the "upstream service failed" semantics clear so
        # the frontend can surface a retry rather than a 4xx that
        # implies the request itself was wrong.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"reason": "smtp_send_failed"},
        )
    return {"status": "sent"}


# ----------------------------------------------------------------
# LIVE STREAM — Server-Sent Events for progress + log tail
# ----------------------------------------------------------------
#
# Two flavours of events flow through the stream:
#
# * ``event: snapshot`` — fired once at connect with the latest task's
#   current_phase / progress_pct / status. Lets a freshly-loaded page
#   render the bar at the right position before the worker emits its
#   next progress update.
# * ``event: progress`` — every ``task-progress`` from the worker. The
#   payload includes ``phase``, ``phase_index``, ``total_phases``,
#   ``progress_pct``, ``message``.
# * ``event: log`` — every ``task-log`` from the worker. The payload
#   is the LogEntry dict (timestamp, level, category, message, plus
#   tool/streaming flags for streaming subprocess lines).
# * ``event: overflow`` — emitted by the in-process pubsub when a
#   slow consumer overran its bounded queue.
# * comment lines starting with ``:`` are SSE keepalive pings.
#
# The stream stays open until the deployment reaches a terminal state
# (success/failed/cancelled), the client disconnects, or the backend
# shuts down. There's no client-driven close — EventSource handles
# reconnect automatically.
@router.get("/{deployment_id}/stream")
async def stream_deployment_events(
    deployment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """Live progress + log stream for one deployment as Server-Sent Events.

    The connection is authenticated with the same Keycloak dependency
    used elsewhere; the standard auth middleware also vets the token
    before this handler runs. After auth we attach to the in-process
    pubsub for this deployment and forward every event to the client.
    """
    deployment = crud_deployments.get_deployment(db, deployment_id)
    if not deployment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    ensure_deployment_access(deployment, current_user, db)
    # The live stream surfaces task-log lines (raw worker stdout
    # incl. terraform output, packer build chatter, etc.). Restrict
    # to the owner view — members get the deployment metadata but
    # not the operational firehose.
    ensure_deployment_owner_view(deployment, current_user)

    # Snapshot the latest task once before subscribing so the client
    # gets a meaningful initial state. Reading happens before the
    # generator yields its first chunk to avoid the "subscribed but
    # nothing buffered yet" gap.
    latest_task = crud_deployments.get_latest_task(db, deployment_id)
    snapshot_payload = {
        "task_id": str(latest_task.taskId) if latest_task else None,
        "status": latest_task.status.value if latest_task else None,
        "current_phase": getattr(latest_task, "current_phase", None),
        "progress_pct": getattr(latest_task, "progress_pct", None),
        "type": latest_task.type.value if latest_task else None,
    }
    initial_status = latest_task.status if latest_task else None

    deployment_id_str = str(deployment_id)

    async def event_stream() -> AsyncIterator[bytes]:
        queue = pubsub.subscribe(deployment_id_str)
        try:
            yield _sse_frame("snapshot", snapshot_payload)

            # Backfill what's been happening lately. The pubsub keeps a
            # bounded ring buffer of recent events per deployment so a
            # client connecting mid-stream sees the last few minutes of
            # progress / log output instead of an empty tail until the
            # next worker line lands. Replays the buffer in order so
            # ``streamCurrentPhaseIndex``/``streamProgress`` end up at
            # their latest values before the live loop starts.
            for past_event in pubsub.recent(deployment_id_str):
                event_name = _event_name_for(past_event.get("type"))
                yield _sse_frame(event_name, past_event)

            # If the task is already in a terminal state we still yield
            # the snapshot but close the stream right away — no live
            # events will ever arrive for this deployment.
            if initial_status in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return

            # Heartbeat / event-pump loop. Wait up to 15s for an event;
            # if nothing arrives, send a ``: keepalive`` comment so
            # proxies and the EventSource client don't time out.
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield b": keepalive\n\n"
                    continue

                event_name = _event_name_for(event.get("type"))
                yield _sse_frame(event_name, event)

                # Stop streaming once the parent task reaches a
                # terminal state. The lifecycle events (succeeded /
                # failed / revoked) flow through the same pubsub key,
                # so we look for them right here. Without this break
                # the connection would dangle until the client closes
                # it.
                if event.get("type") in ("task-succeeded", "task-failed", "task-revoked"):
                    return
        except asyncio.CancelledError:
            # FastAPI cancels the generator on client disconnect.
            raise
        finally:
            pubsub.unsubscribe(deployment_id_str, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
            "Connection": "keep-alive",
        },
    )


_EVENT_NAME_MAP: dict[str, str] = {
    "task-progress": "progress",
    "task-log": "log",
    "task-overflow": "overflow",
    "task-started": "started",
    "task-succeeded": "succeeded",
    "task-failed": "failed",
    "task-revoked": "revoked",
}


def _event_name_for(celery_event_type: str | None) -> str:
    """Map Celery event type names onto short SSE event names.

    Frontend code attaches listeners by these short names rather than
    the verbose celery-internal ones; ``_EVENT_NAME_MAP`` is the
    single source of truth on both sides of the wire.
    """
    return _EVENT_NAME_MAP.get(celery_event_type or "", "message")


def _sse_frame(event_name: str, payload: dict) -> bytes:
    """Serialise one SSE frame.

    SSE format:

    ```
    event: <name>\\n
    data: <json>\\n
    \\n
    ```

    Embedded newlines in the JSON would split the frame into multiple
    ``data:`` lines per the SSE spec; we use ``json.dumps`` defaults
    which keep everything on one line.
    """
    body = json.dumps(payload, default=str)
    return f"event: {event_name}\ndata: {body}\n\n".encode()
