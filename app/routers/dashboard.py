from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import App, Course, Deployment, Task, TaskStatus, User
from app.utils.keycloak_auth import get_current_user_keycloak

router = APIRouter()


class DashboardStatsResponse(BaseModel):
    deployments: int
    apps: int
    courses: int


@router.get("/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """
    Aggregate counts for the dashboard KPI strip.

    Cheap DB-only aggregates — intentionally separated from the OpenStack
    quota call (`GET /quotas/overview`) so the dashboard renders fast even
    when the OpenStack API is slow or unavailable.
    """
    latest_task_subq = (
        db.query(
            Task.deploymentId.label("deployment_id"),
            Task.status.label("status"),
            func.row_number()
            .over(partition_by=Task.deploymentId, order_by=desc(Task.created_at))
            .label("rn"),
        )
        .subquery()
    )

    deployments_running = (
        db.query(func.count(Deployment.deploymentId))
        .join(latest_task_subq, latest_task_subq.c.deployment_id == Deployment.deploymentId)
        .filter(Deployment.userId == current_user.userId)
        .filter(latest_task_subq.c.rn == 1)
        .filter(latest_task_subq.c.status == TaskStatus.RUNNING)
        .scalar()
    ) or 0

    apps_total = (
        db.query(func.count(App.appId))
        .filter(App.userId == current_user.userId)
        .scalar()
    ) or 0

    courses_total = db.query(func.count(Course.courseId)).scalar() or 0

    return DashboardStatsResponse(
        deployments=deployments_running,
        apps=apps_total,
        courses=courses_total,
    )
