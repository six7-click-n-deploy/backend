from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Any, Dict
from uuid import UUID
import os
import re
import logging

from app.database import get_db
from app.models import User
from app.schemas import AppCreate, AppUpdate, AppResponse, AppWithUser, AppWithVersions
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import ensure_resource_access
from app.utils.app_image import parse_image_data_url, build_image_data_url
from app.crud import apps as crud_apps
from app.services.git_service import git_service


def _serialize_app(app):
    """Replace ``app.image`` (bytes) with the data-URL form in-place.

    The ORM model carries the raw bytes plus a separate mime column.
    The Pydantic ``AppResponse`` schema declares ``image: Optional[str]``
    and uses ``from_attributes=True``, so Pydantic reads ``app.image``
    directly. Overwriting that attribute with the rebuilt data-URL
    means the response serialiser sees a string and the wire format
    matches the schema. Returns ``app`` so callers can chain.
    """
    if app is None:
        return None
    raw_bytes = getattr(app, "image", None)
    if isinstance(raw_bytes, (bytes, memoryview, bytearray)):
        app.image = build_image_data_url(bytes(raw_bytes), getattr(app, "image_mime", None))
    return app

router = APIRouter()


# ----------------------------------------------------------------
# HELPER FUNCTIONS FOR PARSING HCL VARIABLES
# ----------------------------------------------------------------
def _detect_openstack_enum(var_name: str, description: str) -> str | None:
    """Detect if variable is an OpenStack resource enum"""
    var_lower = var_name.lower()
    desc_lower = description.lower()
    
    # Check for network-related variables
    if any(keyword in var_lower for keyword in ['network', 'net_id', 'subnet']):
        return "network"
    
    # Check for flavor/instance type
    if any(keyword in var_lower for keyword in ['flavor', 'instance_type', 'instance_size']):
        return "flavor"
    
    # Check for security groups
    if 'security' in var_lower and 'group' in var_lower:
        return "security_group"
    
    # Check for floating IP pool
    if 'floating' in var_lower and ('ip' in var_lower or 'pool' in var_lower):
        return "floating_ip_pool"
    
    # Check for image
    if any(keyword in var_lower for keyword in ['image', 'image_id', 'image_name']):
        return "image"
    
    # Check for keypair
    if any(keyword in var_lower for keyword in ['keypair', 'key_pair', 'ssh_key']):
        return "keypair"
    
    # Check for volume
    if 'volume' in var_lower:
        return "volume"
    
    # Check description for hints
    if 'network' in desc_lower and 'uuid' in desc_lower:
        return "network"
    if 'flavor' in desc_lower or 'instance type' in desc_lower:
        return "flavor"
    if 'security group' in desc_lower:
        return "security_group"
    
    return None


def _parse_terraform_variables(file_path: str) -> List[Dict[str, Any]]:
    """Parse Terraform `variables.tf` file"""
    with open(file_path, 'r') as f:
        content = f.read()
    
    variables = []
    # Regex to match variable blocks: variable "name" { ... }
    pattern = r'variable\s+"([^"]+)"\s*\{([^}]+)\}'
    
    for match in re.finditer(pattern, content, re.DOTALL):
        var_name = match.group(1)
        var_block = match.group(2)
        # Filter: users und image_name rauslassen
        if var_name == "users" or var_name == "image_name":
            continue
        # Extract type
        type_match = re.search(r'type\s*=\s*([^\n]+)', var_block)
        var_type = type_match.group(1).strip() if type_match else "string"
        
        # Extract description
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
        description = desc_match.group(1) if desc_match else ""
        
        # Extract default value
        default_match = re.search(r'default\s*=\s*([^\n]+)', var_block)
        default_value = default_match.group(1).strip() if default_match else None
        
        # Remove surrounding quotes from string literals to prevent double-escaping
        if default_value and default_value.startswith('"') and default_value.endswith('"'):
            default_value = default_value[1:-1]
        
        # Check if required (no default = required)
        required = default_value is None
        
        # Detect OpenStack enum type
        openstack_type = _detect_openstack_enum(var_name, description)
        
        var_info = {
            "name": var_name,
            "type": var_type,
            "description": description,
            "default": default_value,
            "required": required,
            "source": "terraform"
        }
        
        if openstack_type:
            var_info["openstack_type"] = openstack_type
        
        variables.append(var_info)
    
    return variables


def _parse_packer_variables(file_path: str) -> List[Dict[str, Any]]:
    """Parse Packer `variables.pkr.hcl` file"""
    with open(file_path, 'r') as f:
        content = f.read()
    
    variables = []
    # Packer uses similar syntax: variable "name" { ... }
    pattern = r'variable\s+"([^"]+)"\s*\{([^}]+)\}'
    
    for match in re.finditer(pattern, content, re.DOTALL):
        var_name = match.group(1)
        var_block = match.group(2)
        # Filter: image_name rauslassen
        if var_name == "image_name":
            continue
        # Extract type
        type_match = re.search(r'type\s*=\s*([^\n]+)', var_block)
        var_type = type_match.group(1).strip() if type_match else "string"
        
        # Extract description
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
        description = desc_match.group(1) if desc_match else ""
        
        # Extract default value
        default_match = re.search(r'default\s*=\s*([^\n]+)', var_block)
        default_value = default_match.group(1).strip() if default_match else None
        
        # Remove surrounding quotes from string literals to prevent double-escaping
        if default_value and default_value.startswith('"') and default_value.endswith('"'):
            default_value = default_value[1:-1]
        
        # Check if required
        required = default_value is None
        
        # Detect OpenStack enum type
        openstack_type = _detect_openstack_enum(var_name, description)
        
        var_info = {
            "name": var_name,
            "type": var_type,
            "description": description,
            "default": default_value,
            "required": required,
            "source": "packer"
        }
        
        if openstack_type:
            var_info["openstack_type"] = openstack_type
        
        variables.append(var_info)
    
    return variables


# ----------------------------------------------------------------
# GET ALL APPS
# ----------------------------------------------------------------
@router.get("/", response_model=List[AppResponse])
def list_apps(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get all apps owned by the current user.

    Listing is always scoped to the requester regardless of role — teachers
    and admins still only see their own apps in the index. Cross-user access
    happens explicitly through `GET /apps/{app_id}`, which is gated by
    `ensure_resource_access`.
    """
    apps = crud_apps.get_apps(db, skip=skip, limit=limit, user_id=current_user.userId)
    return [_serialize_app(a) for a in apps]


# ----------------------------------------------------------------
# GET APP BY ID
# ----------------------------------------------------------------
@router.get("/{app_id}", response_model=AppWithVersions)
def get_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """Get app by ID with available versions.

    Soft-deleted apps are still readable here so existing deployments
    that still reference them can render their app name, git link,
    etc. They just don't show up in the apps list / deploy wizard
    (those use the default-filtered ``get_apps``).
    """
    app = crud_apps.get_app(db, app_id, include_deleted=True)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    # Fetch versions if git_link exists. Skipped for soft-deleted apps
    # — listing versions is a "what could I deploy" affordance and the
    # answer is "nothing", you already deleted this app.
    if app.git_link and app.deleted_at is None:
        try:
            app.versions = git_service.get_versions(app.git_link)
        except Exception as e:
            app.versions = []
            import logging
            logging.getLogger(__name__).warning(f"Could not fetch versions: {str(e)}")
    else:
        app.versions = []

    return _serialize_app(app)


# ----------------------------------------------------------------
# GET APP VARIABLES
# ----------------------------------------------------------------
@router.get("/{app_id}/variables", response_model=List[Dict[str, Any]])
def get_app_variables(
    app_id: UUID,
    version: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get dynamic app variables from app's Git repository
    Parses variables.tf file and returns all configurable variables
    
    Returns:
    - name: Variable name
    - type: Variable type (string, number, bool, list, map, etc.)
    - description: Variable description
    - default: Default value (if any)
    - required: Whether variable is required
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check access permission
    ensure_resource_access(app.userId, current_user)
    
    if not app.git_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="App has no Git repository configured"
        )
    
    logger = logging.getLogger(__name__)
    deployment_id = f"vars_{app_id}_{version}".replace("/", "_")
    repo_path = None
    
    try:
        # Clone repository with sparse checkout (only variable files)
        repo_path = git_service.clone_release_vars(app.git_link, version, deployment_id)
        
        variables = []
        
        # Parse Terraform variables
        tf_vars_path = os.path.join(repo_path, "terraform", "variables.tf")
        if os.path.exists(tf_vars_path):
            logger.info(f"Parsing Terraform variables from {tf_vars_path}")
            variables.extend(_parse_terraform_variables(tf_vars_path))
        
        # Parse Packer variables
        packer_vars_path = os.path.join(repo_path, "packer", "variables.pkr.hcl")
        if os.path.exists(packer_vars_path):
            logger.info(f"Parsing Packer variables from {packer_vars_path}")
            variables.extend(_parse_packer_variables(packer_vars_path))
        
        if not variables:
            logger.warning(f"No variables found in {repo_path}")
        
        return variables
    
    except Exception:
        logger.exception(f"Failed to get variables for app {app_id} version {version}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch variables",
        )
    finally:
        # Always cleanup cloned repository
        if repo_path:
            try:
                git_service.cleanup_repository(repo_path)
                logger.info(f"Cleaned up repository at {repo_path}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup repository: {str(cleanup_error)}")


# ----------------------------------------------------------------
# CREATE APP
# ----------------------------------------------------------------
@router.post("/", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
def create_app(
    app: AppCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Create a new app
    - **All authenticated users** can create apps
    - **Git repository access is verified** before creating the app
    """
    logger = logging.getLogger(__name__)
    # Decode the optional image data-URL up front so a malformed
    # payload fails before we hit Keycloak / Git / DB.
    image_bytes, image_mime = parse_image_data_url(app.image)

    # Verify repository access if git_link is provided
    if app.git_link:
        access_result = git_service.verify_repository_access(app.git_link)
        if not access_result['success']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=access_result['message']
            )
        logger.info(f"Repository access verified for {app.git_link}")

    db_app = crud_apps.create_app(db, app, current_user.userId)
    if image_bytes is not None:
        db_app = crud_apps.set_app_image(db, db_app.appId, image_bytes, image_mime)
    return _serialize_app(db_app)


# ----------------------------------------------------------------
# UPDATE APP
# ----------------------------------------------------------------
@router.put("/{app_id}", response_model=AppResponse)
def update_app(
    app_id: UUID,
    app_update: AppUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Update an app
    - **Owner or Teacher/Admin** can update
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    # Image is set separately because the AppUpdate.image is a
    # data-URL string and update_app excludes it from the bulk
    # ``setattr`` loop. ``image=None`` means "leave unchanged"
    # (Pydantic ``exclude_unset`` semantics) — to actively clear
    # the image, send ``image=""``.
    image_was_provided = "image" in app_update.model_fields_set
    image_bytes, image_mime = (None, None)
    if image_was_provided:
        image_bytes, image_mime = parse_image_data_url(app_update.image)

    updated_app = crud_apps.update_app(db, app_id, app_update)
    if image_was_provided:
        updated_app = crud_apps.set_app_image(db, app_id, image_bytes, image_mime)
    return _serialize_app(updated_app)


# ----------------------------------------------------------------
# DELETE APP
# ----------------------------------------------------------------
@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """Soft-delete an app.

    Sets ``apps.deleted_at`` instead of removing the row, so any
    historical *or* still-running deployment that points at this app
    keeps resolving (the detail page can still render the app name,
    the running terraform state stays operational). The app simply
    stops appearing in listings and the deploy wizard, so no new
    deploys can be started against it. Existing deployments live on
    until the user destroys them individually.

    Owner/Teacher/Admin only (``ensure_resource_access``).
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    success = crud_apps.soft_delete_app(db, app_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    return None
