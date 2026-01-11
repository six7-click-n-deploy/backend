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
from app.crud import apps as crud_apps
from app.services.git_service import git_service

router = APIRouter()


# ----------------------------------------------------------------
# HELPER FUNCTIONS FOR PARSING HCL VARIABLES
# ----------------------------------------------------------------
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
        
        # Extract type
        type_match = re.search(r'type\s*=\s*([^\n]+)', var_block)
        var_type = type_match.group(1).strip() if type_match else "string"
        
        # Extract description
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
        description = desc_match.group(1) if desc_match else ""
        
        # Extract default value
        default_match = re.search(r'default\s*=\s*([^\n]+)', var_block)
        default_value = default_match.group(1).strip() if default_match else None
        
        # Check if required (no default = required)
        required = default_value is None
        
        variables.append({
            "name": var_name,
            "type": var_type,
            "description": description,
            "default": default_value,
            "required": required,
            "source": "terraform"
        })
    
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
        
        # Extract type
        type_match = re.search(r'type\s*=\s*([^\n]+)', var_block)
        var_type = type_match.group(1).strip() if type_match else "string"
        
        # Extract description
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
        description = desc_match.group(1) if desc_match else ""
        
        # Extract default value
        default_match = re.search(r'default\s*=\s*([^\n]+)', var_block)
        default_value = default_match.group(1).strip() if default_match else None
        
        # Check if required
        required = default_value is None
        
        variables.append({
            "name": var_name,
            "type": var_type,
            "description": description,
            "default": default_value,
            "required": required,
            "source": "packer"
        })
    
    return variables


# ----------------------------------------------------------------
# GET ALL APPS
# ----------------------------------------------------------------
@router.get("/", response_model=List[AppResponse])
def list_apps(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get all apps with optional user filter
    - **Students**: Can only see their own apps
    - **Teachers/Admins**: Can see all apps
    """
    # Students can only see their own apps
    if current_user.role.value == "student" and not user_id:
        user_id = current_user.userId
    elif current_user.role.value == "student" and user_id != current_user.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own apps"
        )
    
    apps = crud_apps.get_apps(db, skip=skip, limit=limit, user_id=user_id)
    return apps


# ----------------------------------------------------------------
# GET APP BY ID
# ----------------------------------------------------------------
@router.get("/{app_id}", response_model=AppWithVersions)
def get_app(
    app_id: UUID,
    refresh: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get app by ID with available versions
    
    Query Parameters:
    - refresh: If true, bypass cache and fetch fresh versions from Git
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check access permission
    ensure_resource_access(app.userId, current_user)
    
    # Fetch versions if git_link exists
    if app.git_link:
        try:
            app.versions = git_service.get_versions(app.git_link, refresh=refresh)
        except Exception as e:
            app.versions = []
            import logging
            logging.getLogger(__name__).warning(f"Could not fetch versions: {str(e)}")
    else:
        app.versions = []
    
    return app


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
    
    except Exception as e:
        logger.error(f"Failed to get variables for app {app_id} version {version}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch variables: {str(e)}"
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
    """
    return crud_apps.create_app(db, app, current_user.userId)


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
    
    updated_app = crud_apps.update_app(db, app_id, app_update)
    return updated_app


# ----------------------------------------------------------------
# DELETE APP
# ----------------------------------------------------------------
@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Delete an app
    - **Owner or Teacher/Admin** can delete
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Check access permission
    ensure_resource_access(app.userId, current_user)
    
    success = crud_apps.delete_app(db, app_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    return None
