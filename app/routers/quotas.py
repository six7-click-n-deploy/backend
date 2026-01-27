from fastapi import APIRouter, HTTPException
import openstack
from pydantic import BaseModel

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


def get_openstack_conn():
    """Erstellt OpenStack Connection aus clouds.yaml"""
    return openstack.connect(cloud='openstack')


@router.get("/overview", response_model=QuotaOverviewResponse)
async def get_quota_overview():
    """
    Holt OpenStack Quota-Übersicht für Compute, Storage und Network.
    
    Returns:
        QuotaOverviewResponse mit used/limit/available für alle Ressourcen
    """
    try:
        conn = get_openstack_conn()
        project_id = conn.current_project_id
        
        # === COMPUTE QUOTAS ===
        compute_limits = conn.compute.get_quota_set(project_id)
        compute_usage = conn.compute.get_limits()
        
        compute = ComputeQuotas(
            instances=QuotaItem(
                used=compute_usage.absolute.total_instances_used,
                limit=compute_limits.instances,
                available=compute_limits.instances - compute_usage.absolute.total_instances_used
            ),
            vcpus=QuotaItem(
                used=compute_usage.absolute.total_cores_used,
                limit=compute_limits.cores,
                available=compute_limits.cores - compute_usage.absolute.total_cores_used
            ),
            ram=QuotaItem(
                used=compute_usage.absolute.total_ram_used,
                limit=compute_limits.ram,
                available=compute_limits.ram - compute_usage.absolute.total_ram_used,
                unit="MB"
            )
        )
        
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
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch quotas: {str(e)}"
        )
