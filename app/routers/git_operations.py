from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import User, GitRepository
from app.schemas import GitRepoCreate, GitRepoResponse, GitCloneRequest
from app.utils.auth import get_current_user
from app.services.git_service import git_service

router = APIRouter()

# ----------------------------------------------------------------
# CREATE GIT REPOSITORY ENTRY
# ----------------------------------------------------------------
@router.post("/repos", response_model=GitRepoResponse)
def create_git_repo(
    repo: GitRepoCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Add a new git repository to user's list"""
    
    new_repo = GitRepository(
        user_id=current_user.id,
        name=repo.name,
        url=repo.url,
        branch=repo.branch
    )
    
    db.add(new_repo)
    db.commit()
    db.refresh(new_repo)
    
    return new_repo

# ----------------------------------------------------------------
# LIST USER'S REPOSITORIES
# ----------------------------------------------------------------
@router.get("/repos", response_model=List[GitRepoResponse])
def list_git_repos(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all repositories for current user"""
    repos = db.query(GitRepository).filter(
        GitRepository.user_id == current_user.id
    ).all()
    return repos

# ----------------------------------------------------------------
# GET REPOSITORY BY ID
# ----------------------------------------------------------------
@router.get("/repos/{repo_id}", response_model=GitRepoResponse)
def get_git_repo(
    repo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific repository"""
    repo = db.query(GitRepository).filter(
        GitRepository.id == repo_id,
        GitRepository.user_id == current_user.id
    ).first()
    
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    return repo

# ----------------------------------------------------------------
# CLONE REPOSITORY (Direct - no Celery)
# ----------------------------------------------------------------
@router.post("/clone")
def clone_repository(
    clone_req: GitCloneRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Clone a git repository directly (synchronous)"""
    
    # Get repository from DB
    repo = db.query(GitRepository).filter(
        GitRepository.id == clone_req.repo_id,
        GitRepository.user_id == current_user.id
    ).first()
    
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    # Clone repository
    result = git_service.clone_repository(
        repo_url=repo.url,
        branch=repo.branch,
        repo_id=repo.id
    )
    
    if not result["success"]:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to clone repository: {result['error']}"
        )
    
    # Update repository info in DB
    repo.last_commit = result["commit_short"]
    repo.last_cloned_at = result["cloned_at"]
    db.commit()
    
    return {
        "message": "Repository cloned successfully",
        "repo_id": repo.id,
        "result": result
    }

# ----------------------------------------------------------------
# DELETE REPOSITORY
# ----------------------------------------------------------------
@router.delete("/repos/{repo_id}")
def delete_git_repo(
    repo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a repository entry"""
    repo = db.query(GitRepository).filter(
        GitRepository.id == repo_id,
        GitRepository.user_id == current_user.id
    ).first()
    
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    db.delete(repo)
    db.commit()
    
    return {"message": "Repository deleted successfully"}