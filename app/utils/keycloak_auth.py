"""
Keycloak Authentication & Authorization
Handles token validation and user management with Keycloak
"""
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from keycloak import KeycloakOpenID, KeycloakAdmin, KeycloakAuthenticationError
from jose import jwt, JWTError

from app.config import settings
from app.database import get_db
from app.models import User, UserRole

# ----------------------------------------------------------------
# SECURITY
# ----------------------------------------------------------------
security = HTTPBearer()

# ----------------------------------------------------------------
# KEYCLOAK CLIENT
# ----------------------------------------------------------------
def get_keycloak_client() -> KeycloakOpenID:
    """Get Keycloak OpenID client instance"""
    return KeycloakOpenID(
        server_url=settings.KEYCLOAK_SERVER_URL,
        client_id=settings.KEYCLOAK_CLIENT_ID,
        realm_name=settings.KEYCLOAK_REALM,
        client_secret_key=settings.KEYCLOAK_CLIENT_SECRET,
        verify=True  # Set to False for local dev with self-signed certs
    )

def get_keycloak_admin() -> KeycloakAdmin:
    """Get Keycloak Admin client instance using appstore-backend service account"""
    return KeycloakAdmin(
        server_url=settings.KEYCLOAK_SERVER_URL,
        realm_name=settings.KEYCLOAK_REALM,
        client_id=settings.KEYCLOAK_CLIENT_ID,
        client_secret_key=settings.KEYCLOAK_CLIENT_SECRET,
        verify=True
    )

# ----------------------------------------------------------------
# TOKEN VALIDATION
# ----------------------------------------------------------------
def verify_keycloak_token(token: str) -> dict:
    """
    Verify and decode Keycloak JWT token
    
    Args:
        token: JWT access token from Keycloak
        
    Returns:
        Decoded token payload with user info
        
    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        keycloak_client = get_keycloak_client()
        
        # Option 1: Introspect (validates against Keycloak server - slower but accurate)
        token_info = keycloak_client.introspect(token)
        
        if not token_info.get('active'):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is not active or has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return token_info
        
    except KeycloakAuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

def verify_keycloak_token_offline(token: str) -> dict:
    """
    Verify JWT token offline using public key (faster, but less secure)
    Use this for performance, but introspect for critical operations
    
    Args:
        token: JWT access token from Keycloak
        
    Returns:
        Decoded token payload
        
    Raises:
        HTTPException: If token is invalid
    """
    try:
        keycloak_client = get_keycloak_client()
        
        # Get public key from Keycloak (cached internally)
        public_key = (
            "-----BEGIN PUBLIC KEY-----\n"
            + keycloak_client.public_key()
            + "\n-----END PUBLIC KEY-----"
        )
        
        # Decode and verify token
        options = {
            "verify_signature": True,
            "verify_aud": False,  # Audience verification (optional)
            "verify_exp": True,   # Expiration verification
        }
        
        decoded = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options=options,
        )
        
        return decoded
        
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

# ----------------------------------------------------------------
# ROLE MAPPING
# ----------------------------------------------------------------
def map_keycloak_roles_to_app_role(keycloak_roles: list) -> UserRole:
    """
    Map Keycloak realm roles to app's UserRole enum
    
    Priority: admin > teacher > student
    """
    if "admin" in keycloak_roles:
        return UserRole.ADMIN
    elif "teacher" in keycloak_roles:
        return UserRole.TEACHER
    else:
        return UserRole.STUDENT

# ----------------------------------------------------------------
# USER AUTHENTICATION (DEPENDENCY)
# ----------------------------------------------------------------
def get_current_user_keycloak(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Get current authenticated user from Keycloak token
    
    - Validates token against Keycloak
    - Retrieves or creates user in local DB (Just-in-Time Provisioning)
    - Maps Keycloak roles to app roles
    
    This is the main dependency for protected routes.
    """
    token = credentials.credentials
    
    # Validate token (use offline for performance, introspect for critical ops)
    token_info = verify_keycloak_token_offline(token)
    
    # Extract user info from token
    keycloak_id = token_info.get("sub")  # Keycloak User ID
    email = token_info.get("email")
    username = token_info.get("preferred_username")
    keycloak_roles = token_info.get("realm_access", {}).get("roles", [])
    
    if not keycloak_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user ID (sub)",
        )
    
    # Try to find user by keycloak_id
    user = db.query(User).filter(User.keycloak_id == keycloak_id).first()
    
    if not user:
        # Just-in-Time User Provisioning: Create user if not exists
        app_role = map_keycloak_roles_to_app_role(keycloak_roles)
        
        user = User(
            keycloak_id=keycloak_id,
            email=email or f"{username}@dhbw.de",  # Fallback email
            username=username or keycloak_id,
            role=app_role,
            password=None,  # No password for Keycloak users
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Update role if changed in Keycloak
        app_role = map_keycloak_roles_to_app_role(keycloak_roles)
        if user.role != app_role:
            user.role = app_role
            db.commit()
            db.refresh(user)
    
    return user

# ----------------------------------------------------------------
# LEGACY SUPPORT (for gradual migration)
# ----------------------------------------------------------------
def get_current_user_hybrid(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Hybrid authentication: Try Keycloak first, fallback to legacy JWT
    Use this during migration period to support both auth methods
    """
    token = credentials.credentials
    
    keycloak_error = None
    legacy_error = None
    
    # Try Keycloak first
    if settings.KEYCLOAK_ENABLED:
        try:
            return get_current_user_keycloak(credentials, db)
        except HTTPException as e:
            keycloak_error = e.detail
    
    # Fallback to legacy JWT auth (import from old auth.py)
    from app.utils.auth import verify_token, get_current_user
    try:
        username = verify_token(credentials)
        user = db.query(User).filter(User.username == username).first()
        if user:
            return user
        legacy_error = "User not found in database"
    except Exception as e:
        legacy_error = str(e)
    
    # Both methods failed - provide detailed error
    error_details = []
    if keycloak_error:
        error_details.append(f"Keycloak: {keycloak_error}")
    if legacy_error:
        error_details.append(f"Legacy: {legacy_error}")
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Authentication failed. {' | '.join(error_details)}",
        headers={"WWW-Authenticate": "Bearer"},
    )

# ----------------------------------------------------------------
# KEYCLOAK USER SEARCH
# ----------------------------------------------------------------
def search_keycloak_users(search_query: str, max_results: int = 10) -> list[dict]:
    """
    Search users in Keycloak by username or email
    Uses service account client (OAuth2 client_credentials grant)
    
    Args:
        search_query: Search string (matches username, email, first/last name)
        max_results: Maximum number of results to return
        
    Returns:
        List of user dicts with: id, username, email, firstName, lastName
        
    Raises:
        HTTPException: If Keycloak query fails
    """
    try:
        keycloak_admin = get_keycloak_admin()
        
        # Search users in Keycloak
        users = keycloak_admin.get_users({
            "search": search_query,
            "max": max_results
        })
        
        # Return simplified user info
        return [{
            "id": user.get("id"),
            "username": user.get("username"),
            "email": user.get("email"),
            "firstName": user.get("firstName", ""),
            "lastName": user.get("lastName", ""),
            "enabled": user.get("enabled", True)
        } for user in users]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search Keycloak users: {str(e)}"
        )


def get_keycloak_users_by_ids(ids: list[str]) -> dict:
    """
    Fetch Keycloak user info for a list of Keycloak IDs.
    Returns a mapping id -> simplified user dict {id, username, email, firstName, lastName}
    """
    result: dict = {}
    if not ids:
        return result
    try:
        kc = get_keycloak_admin()
        for kid in ids:
            try:
                user = kc.get_user(kid)
            except Exception:
                # Some Keycloak clients expose get_user or require different call; try get_users fallback
                try:
                    users = kc.get_users({"search": kid, "max": 1})
                    user = users[0] if users else None
                except Exception:
                    user = None
            if not user:
                continue
            result[user.get("id") or kid] = {
                "id": user.get("id") or kid,
                "username": user.get("username"),
                "email": user.get("email"),
                "firstName": user.get("firstName", ""),
                "lastName": user.get("lastName", ""),
            }
        return result
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch Keycloak users: {str(e)}")
