import os
import shutil
import logging
from typing import Optional, List, Dict, Any
import git
from ..config import settings
import time

logger = logging.getLogger(__name__)

class GitService:
    def __init__(self):
        self.base_path = settings.TEMP_REPO_BASE_PATH
        self._version_cache = {}  # {git_url: (versions, timestamp)}
        self._cache_ttl = 3600  # 1 hour cache

    def clone_release_vars(self, git_url: str, tag: str, deployment_id: str) -> str:
        """
        Klone nur bestimmte Dateien (variables.tf und variables.pkr.hcl) mit Sparse-Checkout.
        Args:
            git_url: URL des Git-Repos
            tag: Tag/Release-Name
            deployment_id: Eindeutige ID für den Zielordner
        Returns:
            Pfad zum geklonten Repo
        Raises:
            Exception with detailed error message
        """
        repo_path = os.path.join(self.base_path, f"deploy_{deployment_id}")
        if os.path.exists(repo_path):
            logger.info(f"Removing existing repo at {repo_path}")
            shutil.rmtree(repo_path)
        try:
            logger.info(f"Cloning repository {git_url}")
            logger.info(f"Target: {repo_path}")
            logger.info(f"Branch/Tag: {tag}")
            logger.info(f"Mode: Sparse checkout (only variables files)")
            
            # Initialize repo with sparse-checkout
            repo = git.Repo.init(repo_path)
            origin = repo.create_remote('origin', git_url)
            
            # Configure sparse checkout to only get specific files
            git_dir = os.path.join(repo_path, '.git')
            sparse_checkout_file = os.path.join(git_dir, 'info', 'sparse-checkout')
            
            os.makedirs(os.path.dirname(sparse_checkout_file), exist_ok=True)
            with open(sparse_checkout_file, 'w') as f:
                f.write('terraform/variables.tf\n')
                f.write('packer/variables.pkr.hcl\n')
            
            # Enable sparse checkout
            config_file = os.path.join(git_dir, 'config')
            with open(config_file, 'a') as f:
                f.write('[core]\n')
                f.write('\tsparseCheckout = true\n')
            
            # Fetch and checkout the tag
            origin.fetch(tag, depth=1)
            repo.heads.master.set_tracking_branch(origin.refs[tag])
            repo.heads.master.checkout(force=True)
            
            logger.info(f"✓ Repository cloned successfully to {repo_path}")
            logger.info(f"Current HEAD: {repo.head.commit.hexsha}")
            
            return repo_path
        except git.exc.GitCommandError as e:
            error_msg = f"Git command failed: {e.stderr if hasattr(e, 'stderr') else str(e)}"
            logger.error(error_msg)
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Failed to clone release {tag} from {git_url}: {str(e)}"
            logger.error(error_msg)
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            raise Exception(error_msg)
    
    def get_versions(self, git_url: str, refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Hole alle verfügbaren Versionen (Tags) eines Git-Repos via ls-remote
        Nutzt Caching um wiederholte Requests zu beschleunigen
        
        Args:
            git_url: URL des Git-Repos
            refresh: Wenn True, Cache ignorieren und neu fetchen
        Returns:
            Liste von Versionen mit Name, Commit-SHA und Type
        Raises:
            Exception with detailed error message
        """
        # Check Cache (außer wenn refresh=True)
        if not refresh and git_url in self._version_cache:
            versions, timestamp = self._version_cache[git_url]
            if time.time() - timestamp < self._cache_ttl:
                age = int(time.time() - timestamp)
                logger.info(f"✓ Using cached versions for {git_url} (age: {age}s, TTL: {self._cache_ttl}s)")
                return versions
        
        try:
            logger.info(f"Fetching versions from {git_url}")
            
            # Nutze git ls-remote um alle Tags zu holen (schneller & zuverlässiger)
            git_cmd = git.cmd.Git()
            refs = git_cmd.ls_remote('--tags', git_url)
            
            versions = []
            seen_tags = set()
            
            # Parse ls-remote output
            for line in refs.split('\n'):
                if not line.strip():
                    continue
                
                parts = line.split()
                if len(parts) < 2:
                    continue
                
                commit = parts[0][:8]
                ref_path = parts[1]
                
                # Filter nur Tags (nicht commit-refs like ^{})
                if 'refs/tags/' in ref_path and not ref_path.endswith('^{}'):
                    tag_name = ref_path.replace('refs/tags/', '')
                    
                    # Duplikate vermeiden
                    if tag_name not in seen_tags:
                        seen_tags.add(tag_name)
                        versions.append({
                            "version": tag_name,
                            "commit": commit,
                            "type": "tag"
                        })
            
            # Sortiere Versionen (neueste zuerst)
            try:
                versions.sort(
                    key=lambda x: tuple(map(int, x['version'].lstrip('v').split('.'))),
                    reverse=True
                )
            except (ValueError, AttributeError):
                # Falls nicht als Versionen parsbar, alphabetisch sortieren
                versions.sort(key=lambda x: x['version'], reverse=True)
            
            # Cache speichern
            self._version_cache[git_url] = (versions, time.time())
            
            logger.info(f"✓ Found {len(versions)} versions: {[v['version'] for v in versions]}")
            return versions
        
        except Exception as e:
            error_msg = f"Failed to fetch versions from {git_url}: {str(e)}"
            logger.error(error_msg)
            logger.exception(e)  # Log full traceback for debugging
            raise Exception(error_msg)

    def cleanup_repository(self, repo_path: str) -> None:
        """Löscht das geklonte Repo"""
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)
            
# Singleton
git_service = GitService()