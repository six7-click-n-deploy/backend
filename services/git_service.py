import git
from pathlib import Path
import shutil
from typing import Dict, Optional
from datetime import datetime

class GitService:
    """Service for Git operations"""
    
    def __init__(self, base_dir: str = "/tmp/repos"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def clone_repository(
        self,
        repo_url: str,
        branch: str = "main",
        repo_id: Optional[int] = None,
        depth: int = 1
    ) -> Dict:
        """
        Clone a git repository
        
        Args:
            repo_url: Git repository URL
            branch: Branch to clone
            repo_id: Optional repository ID for directory naming
            depth: Clone depth (1 for shallow clone)
            
        Returns:
            Dictionary with clone information
        """
        try:
            # Create unique directory
            if repo_id:
                repo_dir = self.base_dir / f"repo_{repo_id}"
            else:
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                repo_dir = self.base_dir / f"repo_{timestamp}"
            
            # Remove if exists
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            
            repo_dir.mkdir(parents=True, exist_ok=True)
            
            # Clone repository
            repo = git.Repo.clone_from(
                repo_url,
                repo_dir,
                branch=branch,
                depth=depth
            )
            
            # Get repository information
            commit = repo.head.commit
            
            # Count files
            files_count = len(list(repo_dir.rglob("*")))
            
            return {
                "success": True,
                "repo_url": repo_url,
                "branch": branch,
                "commit_hash": commit.hexsha,
                "commit_short": commit.hexsha[:7],
                "commit_message": commit.message.strip(),
                "commit_author": str(commit.author),
                "commit_date": commit.committed_datetime.isoformat(),
                "files_count": files_count,
                "repo_path": str(repo_dir),
                "cloned_at": datetime.utcnow().isoformat()
            }
            
        except git.GitCommandError as e:
            return {
                "success": False,
                "error": f"Git error: {str(e)}",
                "repo_url": repo_url,
                "branch": branch
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "repo_url": repo_url,
                "branch": branch
            }
    
    def get_repository_info(self, repo_path: str) -> Dict:
        """Get information about a local repository"""
        try:
            repo = git.Repo(repo_path)
            commit = repo.head.commit
            
            return {
                "success": True,
                "commit_hash": commit.hexsha,
                "commit_short": commit.hexsha[:7],
                "commit_message": commit.message.strip(),
                "commit_author": str(commit.author),
                "commit_date": commit.committed_datetime.isoformat(),
                "branch": repo.active_branch.name,
                "remotes": [remote.name for remote in repo.remotes],
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def pull_repository(self, repo_path: str) -> Dict:
        """Pull latest changes from remote"""
        try:
            repo = git.Repo(repo_path)
            origin = repo.remotes.origin
            origin.pull()
            
            commit = repo.head.commit
            
            return {
                "success": True,
                "message": "Repository updated successfully",
                "commit_hash": commit.hexsha,
                "commit_short": commit.hexsha[:7]
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def cleanup_repository(self, repo_path: str) -> Dict:
        """Remove a cloned repository"""
        try:
            path = Path(repo_path)
            if path.exists():
                shutil.rmtree(path)
                return {
                    "success": True,
                    "message": f"Repository removed: {repo_path}"
                }
            else:
                return {
                    "success": False,
                    "error": "Repository path does not exist"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

# Singleton instance
git_service = GitService()