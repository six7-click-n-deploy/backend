# ----------------------------------------------------------------
from sqlalchemy.orm import Session

from app.models import User, UserRole


# ZENTRALE USER-SYNC-FUNKTION
# ----------------------------------------------------------------
def sync_user_from_keycloak(db: Session, keycloak_user_data: dict) -> User:
    """
    Synchronisiert einen User aus Keycloak-Daten in die lokale DB.
    Legt an, falls nicht vorhanden, oder updated Rolle und Namen, falls nötig.
    Erwartet keycloak_user_data mit Feldern: id, username, email, roles (Liste), firstName, lastName
    """
    from app.models import User
    # Keycloak User ID
    keycloak_id = keycloak_user_data.get("id") or keycloak_user_data.get("sub")
    email = keycloak_user_data.get("email") or f"{keycloak_user_data.get('username')}@dhbw.de"
    username = keycloak_user_data.get("username") or keycloak_id
    keycloak_roles = keycloak_user_data.get("roles") or keycloak_user_data.get("realm_access", {}).get("roles", [])
    app_role = map_keycloak_roles_to_app_role(keycloak_roles)
    first_name = keycloak_user_data.get("firstName") or keycloak_user_data.get("given_name")
    last_name = keycloak_user_data.get("lastName") or keycloak_user_data.get("family_name")

    user = db.query(User).filter(User.keycloak_id == keycloak_id).first()
    if not user:
        user = User(
            keycloak_id=keycloak_id,
            email=email,
            username=username,
            role=app_role,
            firstName=first_name,
            lastName=last_name
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Update Rolle, falls sie sich geändert hat
        updated = False
        if user.role != app_role:
            user.role = app_role
            updated = True
        # Update Namen, falls sie sich geändert haben
        if (first_name and user.firstName != first_name):
            user.firstName = first_name
            updated = True
        if (last_name and user.lastName != last_name):
            user.lastName = last_name
            updated = True
        if updated:
            db.commit()
            db.refresh(user)
    return user
"""
Keycloak Authentication & Authorization
Handles token validation and user management with Keycloak
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from keycloak import KeycloakAdmin, KeycloakAuthenticationError, KeycloakOpenID
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

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
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

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
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

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
    # Try to get first/last name from all possible claim names
    first_name = token_info.get("firstName") or token_info.get("given_name")
    last_name = token_info.get("lastName") or token_info.get("family_name")

    if not keycloak_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user ID (sub)",
        )

    # Zentrale Sync-Funktion nutzen
    user = sync_user_from_keycloak(db, {
        "id": keycloak_id,
        "email": email,
        "username": username,
        "roles": keycloak_roles,
        "firstName": first_name,
        "lastName": last_name
    })
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
    keycloak_error = None
    legacy_error = None

    # Try Keycloak first
    if settings.KEYCLOAK_ENABLED:
        try:
            return get_current_user_keycloak(credentials, db)
        except HTTPException as e:
            keycloak_error = e.detail

    # Fallback to legacy JWT auth (import from old auth.py)
    from app.utils.auth import verify_token
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
        ) from e


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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch Keycloak users: {str(e)}") from e
