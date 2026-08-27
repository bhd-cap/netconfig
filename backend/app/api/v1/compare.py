"""
Configuration Comparison API endpoints
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, get_organization_id
from app.models.user import User
from app.repositories.configuration import ConfigurationRepository
from app.repositories.device import DeviceRepository
from app.repositories.audit_log import AuditLogRepository
from app.services.config_comparison import config_comparison
from app.services.storage import StorageError
from pydantic import BaseModel


router = APIRouter()


class CompareRequest(BaseModel):
    """Request schema for comparing configurations"""
    config1_id: int
    config2_id: int
    context_lines: int = 3
    include_html: bool = False


class CompareResponse(BaseModel):
    """Response schema for configuration comparison"""
    is_identical: bool
    unified_diff: str
    html_diff: Optional[str] = None
    structured_diff: list
    statistics: dict
    config1: dict
    config2: dict


@router.post("/", response_model=CompareResponse)
def compare_configurations(
    compare_request: CompareRequest,
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Compare two device configurations

    Args:
        compare_request: Configuration IDs and comparison options
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        CompareResponse: Comparison results with diff and statistics

    Raises:
        HTTPException: If configurations not found or access denied
    """
    config_repo = ConfigurationRepository(db)
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    # Get both configurations
    config1 = config_repo.get(compare_request.config1_id)
    config2 = config_repo.get(compare_request.config2_id)

    if not config1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration {compare_request.config1_id} not found",
        )

    if not config2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration {compare_request.config2_id} not found",
        )

    # Verify both configs are from the same device before any device lookup,
    # so the mismatch case costs no queries at all.
    if config1.device_id != config2.device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configurations must be from the same device",
        )

    # Verify the device belongs to organization
    device1 = device_repo.get_by_id_and_organization(config1.device_id, organization_id)

    if not device1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Perform comparison
    try:
        result = config_comparison.compare_configs(
            config1_path=config1.file_path,
            config2_path=config2.file_path,
            config1_label=f"{device1.hostname} - {config1.backed_up_at.strftime('%Y-%m-%d %H:%M:%S')}",
            config2_label=f"{device1.hostname} - {config2.backed_up_at.strftime('%Y-%m-%d %H:%M:%S')}",
            context_lines=compare_request.context_lines,
            include_html=compare_request.include_html,
        )

        # Log comparison
        audit_repo.log_action(
            user_id=current_user.id,
            action="configurations_compared",
            resource_type="configuration",
            details={
                "config1_id": config1.id,
                "config2_id": config2.id,
                "device_id": config1.device_id,
                "device_hostname": device1.hostname,
                "is_identical": result["is_identical"],
                "total_changes": result["statistics"]["total_changes"],
            },
        )

        return result

    except (StorageError, FileNotFoundError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stored configuration file is unavailable: {str(e)}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error comparing configurations: {str(e)}",
        )


@router.get("/device/{device_id}/latest-vs-previous")
def compare_latest_vs_previous(
    device_id: int,
    context_lines: int = Query(3, ge=0, le=10),
    include_html: bool = Query(False),
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Compare the latest two configurations for a device

    Args:
        device_id: Device ID
        context_lines: Number of context lines around changes
        include_html: Include HTML diff in response
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        CompareResponse: Comparison results

    Raises:
        HTTPException: If device not found, insufficient configs, or access denied
    """
    config_repo = ConfigurationRepository(db)
    device_repo = DeviceRepository(db)

    # Verify device belongs to organization
    device = device_repo.get_by_id_and_organization(device_id, organization_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found or access denied",
        )

    # Get latest two configurations
    configs = config_repo.get_by_device(device_id, skip=0, limit=2)

    if len(configs) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device must have at least 2 configurations to compare",
        )

    # Compare (latest vs previous)
    config1, config2 = configs[1], configs[0]  # Previous, Latest

    try:
        result = config_comparison.compare_configs(
            config1_path=config1.file_path,
            config2_path=config2.file_path,
            config1_label=f"{device.hostname} - {config1.backed_up_at.strftime('%Y-%m-%d %H:%M:%S')}",
            config2_label=f"{device.hostname} - {config2.backed_up_at.strftime('%Y-%m-%d %H:%M:%S')}",
            context_lines=context_lines,
            include_html=include_html,
        )

        return result

    except (StorageError, FileNotFoundError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stored configuration file is unavailable: {str(e)}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error comparing configurations: {str(e)}",
        )


@router.get("/summary/{config1_id}/{config2_id}")
def get_comparison_summary(
    config1_id: int,
    config2_id: int,
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get a quick summary of changes without full diff

    Args:
        config1_id: First configuration ID
        config2_id: Second configuration ID
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        dict: Change summary

    Raises:
        HTTPException: If configurations not found or access denied
    """
    config_repo = ConfigurationRepository(db)
    device_repo = DeviceRepository(db)

    # Get both configurations
    config1 = config_repo.get(config1_id)
    config2 = config_repo.get(config2_id)

    if not config1 or not config2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both configurations not found",
        )

    # Verify same device first; then one device lookup covers both configs.
    if config1.device_id != config2.device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configurations must be from the same device",
        )

    if not device_repo.get_by_id_and_organization(config1.device_id, organization_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    try:
        summary = config_comparison.get_change_summary(
            config1_path=config1.file_path,
            config2_path=config2.file_path,
        )

        return summary

    except (StorageError, FileNotFoundError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stored configuration file is unavailable: {str(e)}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating comparison summary: {str(e)}",
        )
