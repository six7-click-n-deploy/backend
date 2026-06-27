"""Integration tests for POST /deployments/{id}/teams/{team}/users/{user_id}/resend-access.

These tests cover the access-control matrix and the upstream-failure
mapping for the resend-access endpoint in
``app/routers/deployments.py``. The actual ``deployment_notifier.
resend_user_access`` call is patched out — we only assert that the
endpoint reaches the notifier with the right arguments (or short-
circuits with the correct HTTP code before reaching it).
"""
import json
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

from app.models import (
    App,
    Deployment,
    OpenStackAuthType,
    Task,
    TaskStatus,
    TaskType,
    Team,
    UserToTeam,
    UserOpenStackCredential,
)


def _seed_deployment_with_team(
    db,
    owner,
    member=None,
    *,
    deploy_status: TaskStatus = TaskStatus.SUCCESS,
):
    """Seed an app + deployment + team + (optional) team membership +
    one latest DEPLOY task with the given status. Mirrors the helper
    in test_deployments_pause_resume.py but adds team plumbing so the
    resend endpoint has a valid team/user pair to address.
    """
    from app.utils import crypto

    app = App(
        appId=uuid.uuid4(),
        name=f"app-{uuid.uuid4().hex[:8]}",
        userId=owner.userId,
        git_link="https://example.com/repo.git",
    )
    db.add(app)
    db.flush()

    deployment = Deployment(
        deploymentId=uuid.uuid4(),
        name=f"d-{uuid.uuid4().hex[:8]}",
        appId=app.appId,
        userId=owner.userId,
        releaseTag="v1.0.0",
        userInputVar=json.dumps({"terraform": {}, "packer": {}}),
    )
    db.add(deployment)
    db.flush()

    db.add(
        Task(
            taskId=uuid.uuid4(),
            deploymentId=deployment.deploymentId,
            type=TaskType.DEPLOY,
            status=deploy_status,
            created_at=datetime.utcnow(),
        )
    )

    team = Team(
        teamId=uuid.uuid4(),
        name="Team-1",
        deploymentId=deployment.deploymentId,
    )
    db.add(team)
    db.flush()

    if member is not None:
        db.add(
            UserToTeam(
                userToTeamId=uuid.uuid4(),
                userId=member.userId,
                teamId=team.teamId,
            )
        )

    db.add(
        UserOpenStackCredential(
            credentialId=uuid.uuid4(),
            userId=owner.userId,
            auth_type=OpenStackAuthType.APPLICATION_CREDENTIAL,
            auth_url="https://keystone.example/v3",
            encrypted_identifier=crypto.encrypt("test-id"),
            encrypted_secret=crypto.encrypt("test-secret"),
        )
    )
    db.commit()
    db.refresh(deployment)
    return deployment, team


@pytest.mark.integration
def test_resend_access_owner_dispatches_notifier(client, db, mock_user):
    """Owner (creator) hits the endpoint for some member — notifier
    is called with the URL path arguments and 202 is returned."""
    deployment, team = _seed_deployment_with_team(db, mock_user, member=mock_user)
    target_user = mock_user.userId

    with patch(
        "app.routers.deployments.deployment_notifier.resend_user_access",
        return_value=True,
    ) as m_resend:
        response = client.post(
            f"/deployments/{deployment.deploymentId}"
            f"/teams/{team.teamId}"
            f"/users/{target_user}"
            f"/resend-access"
        )

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "sent"}
    m_resend.assert_called_once()
    # Positional args: (db, deployment_id, team_id, user_id)
    call_args = m_resend.call_args.args
    assert str(call_args[1]) == str(deployment.deploymentId)
    assert str(call_args[2]) == str(team.teamId)
    assert str(call_args[3]) == str(target_user)


@pytest.mark.integration
def test_resend_access_team_member_self_dispatches(client, db, mock_user, mock_student):
    """A plain team member (non-owner-view) may resend the mail for
    themself. The deployment owner is ``mock_user``, the request comes
    from ``mock_student`` via the student_client fixture-pattern, but
    we just override the keycloak dependency inline here.
    """
    deployment, team = _seed_deployment_with_team(
        db, owner=mock_user, member=mock_student,
    )

    # Swap auth to the student so this is the "member self-resend" path.
    from app.main import app as fastapi_app
    from app.utils.keycloak_auth import get_current_user_keycloak
    fastapi_app.dependency_overrides[get_current_user_keycloak] = lambda: mock_student

    with patch(
        "app.routers.deployments.deployment_notifier.resend_user_access",
        return_value=True,
    ) as m_resend:
        response = client.post(
            f"/deployments/{deployment.deploymentId}"
            f"/teams/{team.teamId}"
            f"/users/{mock_student.userId}"
            f"/resend-access"
        )

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "sent"}
    m_resend.assert_called_once()


@pytest.mark.integration
def test_resend_access_admin_can_resend_for_other_user(
    admin_client, db, mock_user, mock_admin,
):
    """Admins get the owner view of every deployment, so they may
    resend the mail for any team member of any deployment — even one
    they didn't create."""
    deployment, team = _seed_deployment_with_team(
        db, owner=mock_user, member=mock_user,
    )

    with patch(
        "app.routers.deployments.deployment_notifier.resend_user_access",
        return_value=True,
    ) as m_resend:
        response = admin_client.post(
            f"/deployments/{deployment.deploymentId}"
            f"/teams/{team.teamId}"
            f"/users/{mock_user.userId}"
            f"/resend-access"
        )

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "sent"}
    m_resend.assert_called_once()


@pytest.mark.integration
def test_resend_access_unrelated_student_403(
    student_client, db, mock_user, mock_student,
):
    """A student who has no relation to the deployment (not in any of
    its teams, no direct UserToDeployment row) must be rejected at the
    ``ensure_deployment_access`` gate with a 403 — not a 404, since
    leaking existence by error code would be an enumeration vector.
    """
    # Owner is mock_user; no membership added for mock_student.
    deployment, team = _seed_deployment_with_team(db, owner=mock_user)

    with patch(
        "app.routers.deployments.deployment_notifier.resend_user_access",
        return_value=True,
    ) as m_resend:
        response = student_client.post(
            f"/deployments/{deployment.deploymentId}"
            f"/teams/{team.teamId}"
            f"/users/{mock_student.userId}"
            f"/resend-access"
        )

    assert response.status_code == 403, response.text
    m_resend.assert_not_called()


@pytest.mark.integration
def test_resend_access_when_notifier_raises_returns_502(client, db, mock_user):
    """When the notifier returns ``False`` (template render OK but
    SMTP rejected the message), the endpoint must surface this as a
    502 Bad Gateway with ``reason=smtp_send_failed`` so the frontend
    can offer a retry instead of a misleading 4xx.
    """
    deployment, team = _seed_deployment_with_team(db, mock_user, member=mock_user)

    with patch(
        "app.routers.deployments.deployment_notifier.resend_user_access",
        return_value=False,
    ) as m_resend:
        response = client.post(
            f"/deployments/{deployment.deploymentId}"
            f"/teams/{team.teamId}"
            f"/users/{mock_user.userId}"
            f"/resend-access"
        )

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["detail"]["reason"] == "smtp_send_failed"
    m_resend.assert_called_once()


@pytest.mark.integration
def test_resend_access_404_for_unknown_user(client, db, mock_user):
    """Notifier raises ``ResendError('user_not_in_team')`` — the
    endpoint maps that ResendError reason to a 404 with the structured
    detail body. Same mapping covers ``team_not_in_deployment`` and
    ``deployment_not_found``; ``user_not_in_team`` is the realistic
    case here since the team exists but a random UUID was passed.
    """
    from app.services import deployment_notifier as notifier_module

    deployment, team = _seed_deployment_with_team(db, mock_user, member=mock_user)
    unknown_user_id = uuid.uuid4()

    with patch(
        "app.routers.deployments.deployment_notifier.resend_user_access",
        side_effect=notifier_module.ResendError("user_not_in_team"),
    ) as m_resend:
        response = client.post(
            f"/deployments/{deployment.deploymentId}"
            f"/teams/{team.teamId}"
            f"/users/{unknown_user_id}"
            f"/resend-access"
        )

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["detail"]["reason"] == "user_not_in_team"
    m_resend.assert_called_once()
