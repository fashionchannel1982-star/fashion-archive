"""
Fashion Archive — Access Control & Provenance Service
Tracks content ownership, usage rights, and access tiers.
Six-tier hierarchy: public → registered → school → pro → partner → internal
"""

from sqlalchemy.ext.asyncio import AsyncSession
from services.database import Provenance
from models.provenance import SourceType, AccessTier
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


async def attach_provenance(
    session: AsyncSession,
    show_id: str,
    source_name: str,
    source_type: str,
    source_url: Optional[str] = None,
    submitted_by: str = "api",
    access_tier: str = AccessTier.public,
    usage_rights: Optional[str] = None,
    embargo_until: Optional[datetime] = None,
    attribution_display: Optional[str] = None,
    restrictions_notes: Optional[str] = None,
) -> Provenance:
    """
    Create a provenance record for a show.
    Called immediately after show creation — before ingestion starts.
    """
    provenance = Provenance(
        show_id=show_id,
        source_name=source_name,
        source_type=str(source_type),
        source_url=source_url,
        submitted_by=submitted_by,
        access_tier=str(access_tier),
        usage_rights=usage_rights,
        embargo_until=embargo_until,
        attribution_display=attribution_display,
        restrictions_notes=restrictions_notes,
    )
    session.add(provenance)
    await session.commit()
    logger.info(f"Provenance attached: show={show_id} source={source_name} tier={access_tier}")
    return provenance


def is_accessible(provenance: Provenance, user_tier: str) -> bool:
    """
    Check if content is accessible for a given user tier.
    Tier hierarchy: public < registered < school < pro < partner < internal
    """
    tier_order = {
        AccessTier.public: 0,
        AccessTier.registered: 1,
        AccessTier.school: 2,
        AccessTier.pro: 3,
        AccessTier.partner: 4,
        AccessTier.internal: 5,
    }

    content_level = tier_order.get(provenance.access_tier, 0)
    user_level = tier_order.get(user_tier, 0)

    # Check embargo
    if provenance.embargo_until and provenance.embargo_until > datetime.utcnow():
        return False

    return user_level >= content_level
