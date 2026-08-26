"""
Database initialization script
Creates initial admin user and default organization

Safe to run repeatedly: it creates what is missing and leaves everything else
alone. Pass --reset-admin-password to force the admin password back to
ADMIN_PASSWORD (used by the installer when re-running against an existing
database).
"""
import argparse
import sys
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, engine
from app.core.config import settings
from app.core.security import get_password_hash
from app.models import Organization, User
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def init_db(db: Session, reset_admin_password: bool = False) -> None:
    """
    Initialize database with default organization and admin user

    Args:
        db: Database session
        reset_admin_password: Reset an existing admin user's password to
            ADMIN_PASSWORD
    """
    # Check if organization already exists
    org = (
        db.query(Organization)
        .filter(Organization.name == settings.ADMIN_ORG_NAME)
        .first()
    )

    if not org:
        # Create default organization
        org = Organization(
            name=settings.ADMIN_ORG_NAME,
            description="Default organization created during initialization",
            is_active=True,
        )
        db.add(org)
        db.commit()
        logger.info(f"[+] Created organization: {org.name} (ID: {org.id})")
    else:
        logger.info(f"[=] Organization already exists: {org.name} (ID: {org.id})")

    # Check if admin user already exists
    admin_user = (
        db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
    )

    if not admin_user:
        # Create admin user
        admin_user = User(
            organization_id=org.id,
            username=settings.ADMIN_USERNAME,
            email=settings.ADMIN_EMAIL,
            hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
            is_active=True,
            is_admin=True,
            is_superuser=True,
        )
        db.add(admin_user)
        db.commit()
        logger.info(f"[+] Created admin user: {admin_user.username} (ID: {admin_user.id})")
        logger.info(f"    Email:    {admin_user.email}")
        logger.info(f"    Password: {settings.ADMIN_PASSWORD}")
        logger.warning("[!] This is a temporary password - change it after first login.")
    elif reset_admin_password:
        admin_user.hashed_password = get_password_hash(settings.ADMIN_PASSWORD)
        admin_user.is_active = True
        admin_user.is_admin = True
        db.commit()
        logger.info(f"[+] Reset password for admin user: {admin_user.username}")
        logger.info(f"    Password: {settings.ADMIN_PASSWORD}")
        logger.warning("[!] This is a temporary password - change it after first login.")
    else:
        logger.info(
            f"[=] Admin user already exists: {admin_user.username} "
            f"(ID: {admin_user.id}); password left unchanged"
        )


def main():
    """Main initialization function"""
    parser = argparse.ArgumentParser(description="Initialize the database")
    parser.add_argument(
        "--reset-admin-password",
        action="store_true",
        help="Reset the admin user's password to ADMIN_PASSWORD if the user exists",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("DATABASE INITIALIZATION")
    logger.info("=" * 60)

    try:
        # Schema is owned by Alembic (`alembic upgrade head`), which the
        # backend container runs on start. Creating tables here as well would
        # produce a schema Alembic does not know it has already applied.
        db = SessionLocal()
        try:
            init_db(db, reset_admin_password=args.reset_admin_password)
            logger.info("=" * 60)
            logger.info("[+] Database initialization completed successfully")
            logger.info("=" * 60)
        finally:
            db.close()

    except Exception as e:
        logger.error(f"[!] Database initialization failed: {str(e)}")
        sys.exit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
