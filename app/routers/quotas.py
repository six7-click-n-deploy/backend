import logging

import openstack
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.crud import openstack_credentials as crud_creds
from app.database import get_db
from app.models import User
from app.utils.keycloak_auth import get_current_user_keycloak

logger = logging.getLogger(__name__)

router = APIRouter()


class QuotaItem(BaseModel):
    used: int
    limit: int
    available: int
    unit: str | None = None


class ComputeQuotas(BaseModel):
    instances: QuotaItem
    vcpus: QuotaItem
    ram: QuotaItem


class StorageQuotas(BaseModel):
    volumes: QuotaItem
    snapshots: QuotaItem
    gigabytes: QuotaItem


class NetworkQuotas(BaseModel):
    floating_ips: QuotaItem
    security_groups: QuotaItem
    security_group_rules: QuotaItem
    networks: QuotaItem
    ports: QuotaItem
    routers: QuotaItem


class QuotaOverviewResponse(BaseModel):
    compute: ComputeQuotas
    storage: StorageQuotas
    network: NetworkQuotas


def _build_connect_kwargs(creds: dict) -> dict:
    base = {
        "auth_url": creds["auth_url"],
        "region_name": creds.get("region_name"),
        "interface": creds.get("interface") or "public",
        "identity_api_version": creds.get("identity_api_version") or "3",
    }
    if creds["auth_type"] == "v3applicationcredential":
        base.update({
            "auth_type": "v3applicationcredential",
            "application_credential_id": creds["identifier"],
            "application_credential_secret": creds["secret"],
        })
    else:
        base.update({
            "auth_type": "password",
            "username": creds["identifier"],
            "password": creds["secret"],
            "project_id": creds.get("project_id"),
            "project_name": creds.get("project_name"),
            "user_domain_name": creds.get("user_domain_name"),
            "project_domain_name": creds.get("project_domain_name") or creds.get("user_domain_name"),
        })
    return base


def _get_openstack_conn_for_user(db: Session, user: User):
    """Build a per-user OpenStack connection from the stored credential row."""
    try:
        creds = crud_creds.get_decrypted_for_backend(db, user.userId)
    except crud_creds.NoCredentialError:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"reason": "openstack_credentials_missing"},
        )
    return openstack.connect(**_build_connect_kwargs(creds))


@router.get("/overview", response_model=QuotaOverviewResponse)
async def get_quota_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """
    Holt OpenStack Quota-Übersicht für Compute, Storage und Network
    aus dem **persönlichen** OpenStack-Projekt des Users.

    Returns:
        QuotaOverviewResponse mit used/limit/available für alle Ressourcen
    """
    try:
        conn = _get_openstack_conn_for_user(db, current_user)
        project_id = conn.current_project_id

        # === COMPUTE QUOTAS ===
        try:
            compute_limits = conn.compute.get_quota_set(project_id)
            compute_usage = conn.compute.get_limits()

            compute = ComputeQuotas(
                instances=QuotaItem(
                    used=getattr(compute_usage.absolute, 'total_instances_used', 0),
                    limit=getattr(compute_limits, 'instances', 0),
                    available=getattr(compute_limits, 'instances', 0) - getattr(compute_usage.absolute, 'total_instances_used', 0)
                ),
                vcpus=QuotaItem(
                    used=getattr(compute_usage.absolute, 'total_cores_used', 0),
                    limit=getattr(compute_limits, 'cores', 0),
                    available=getattr(compute_limits, 'cores', 0) - getattr(compute_usage.absolute, 'total_cores_used', 0)
                ),
                ram=QuotaItem(
                    used=getattr(compute_usage.absolute, 'total_ram_used', 0),
                    limit=getattr(compute_limits, 'ram', 0),
                    available=getattr(compute_limits, 'ram', 0) - getattr(compute_usage.absolute, 'total_ram_used', 0),
                    unit="MB"
                )
            )
        except Exception:
            logger.exception("Failed to fetch compute quotas for user")
            raise HTTPException(status_code=500, detail="Failed to fetch compute quotas")

        # === STORAGE QUOTAS ===
        volume_limits = conn.volume.get_quota_set(project_id)
        volumes = list(conn.volume.volumes())
        snapshots = list(conn.volume.snapshots())
        total_gb_used = sum(v.size for v in volumes)

        storage = StorageQuotas(
            volumes=QuotaItem(
                used=len(volumes),
                limit=volume_limits.volumes,
                available=volume_limits.volumes - len(volumes)
            ),
            snapshots=QuotaItem(
                used=len(snapshots),
                limit=volume_limits.snapshots,
                available=volume_limits.snapshots - len(snapshots)
            ),
            gigabytes=QuotaItem(
                used=total_gb_used,
                limit=volume_limits.gigabytes,
                available=volume_limits.gigabytes - total_gb_used,
                unit="GB"
            )
        )

        # === NETWORK QUOTAS ===
        network_limits = conn.network.get_quota(project_id)

        # Zähle tatsächliche Ressourcen-Nutzung
        floating_ips_used = len(list(conn.network.ips()))
        security_groups_used = len(list(conn.network.security_groups()))
        networks_used = len(list(conn.network.networks()))
        ports_used = len(list(conn.network.ports()))
        routers_used = len(list(conn.network.routers()))

        # Security Group Rules über alle Security Groups zählen
        sg_rules_used = sum(
            len(list(conn.network.security_group_rules(security_group_id=sg.id)))
            for sg in conn.network.security_groups()
        )

        network = NetworkQuotas(
            floating_ips=QuotaItem(
                used=floating_ips_used,
                limit=getattr(network_limits, 'floatingip', 50),
                available=getattr(network_limits, 'floatingip', 50) - floating_ips_used
            ),
            security_groups=QuotaItem(
                used=security_groups_used,
                limit=getattr(network_limits, 'security_group', 10),
                available=getattr(network_limits, 'security_group', 10) - security_groups_used
            ),
            security_group_rules=QuotaItem(
                used=sg_rules_used,
                limit=getattr(network_limits, 'security_group_rule', 100),
                available=getattr(network_limits, 'security_group_rule', 100) - sg_rules_used
            ),
            networks=QuotaItem(
                used=networks_used,
                limit=getattr(network_limits, 'network', 100),
                available=getattr(network_limits, 'network', 100) - networks_used
            ),
            ports=QuotaItem(
                used=ports_used,
                limit=getattr(network_limits, 'port', 500),
                available=getattr(network_limits, 'port', 500) - ports_used
            ),
            routers=QuotaItem(
                used=routers_used,
                limit=getattr(network_limits, 'router', 10),
                available=getattr(network_limits, 'router', 10) - routers_used
            )
        )

        return QuotaOverviewResponse(compute=compute, storage=storage, network=network)

    except HTTPException:
        # Preserve the 412 Precondition Failed when credentials are missing.
        raise
    except Exception:
        logger.exception("Failed to fetch quotas for user")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch quotas",
        )
