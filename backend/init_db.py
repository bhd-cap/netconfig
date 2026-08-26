"""
Database initialization script
Creates initial admin user and default organization
"""
import sys
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, engine, Base
from app.core.config import settings
from app.core.security import get_password_hash
from app.models import Organization, User
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db(db: Session) -> None:
    """Initialize database with default organization and admin user"""

    # Check if organization already exists
    org = db.query(Organization).filter(Organization.name == settings.ADMIN_ORG_NAME).first()

    if not org:
        # Create default organization
        org = Organization(
            name=settings.ADMIN_ORG_NAME,
            description="Default organization created during initialization",
            is_active=True,
        )
        db.add(org)
        db.commit()
        db.refresh(org)
        logger.info(f"✓ Created organization: {org.name} (ID: {org.id})")
    else:
        logger.info(f"✓ Organization already exists: {org.name} (ID: {org.id})")

    # Check if admin user already exists
    admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()

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
        db.refresh(admin_user)
        logger.info(f"✓ Created admin user: {admin_user.username} (ID: {admin_user.id})")
        logger.info(f"  Email: {admin_user.email}")
        logger.info(f"  Password: {settings.ADMIN_PASSWORD}")
        logger.warning("⚠️  CHANGE THE DEFAULT PASSWORD IMMEDIATELY!")
    else:
        logger.info(f"✓ Admin user already exists: {admin_user.username} (ID: {admin_user.id})")


def main():
    """Main initialization function"""
    logger.info("=" * 60)
    logger.info("DATABASE INITIALIZATION")
    logger.info("=" * 60)

    try:
        # Create all tables
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Database tables created")

        # Create session and initialize data
        db = SessionLocal()
        try:
            init_db(db)
            logger.info("=" * 60)
            logger.info("✓ Database initialization completed successfully!")
            logger.info("=" * 60)
            logger.info("")
            logger.info("You can now login with:")
            logger.info(f"  Username: {settings.ADMIN_USERNAME}")
            logger.info(f"  Password: {settings.ADMIN_PASSWORD}")
            logger.info("")
            logger.info("Access the application at:")
            logger.info("  Web UI:    http://localhost")
            logger.info("  API Docs:  http://localhost:8000/docs")
            logger.info("  Flower:    http://localhost:5555")
            logger.info("=" * 60)

        finally:
            db.close()

    except Exception as e:
        logger.error(f"✗ Database initialization failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
