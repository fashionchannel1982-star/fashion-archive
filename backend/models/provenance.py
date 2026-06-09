"""
Fashion Archive — Provenance Models
Enums for source type and access tier.
"""

from enum import Enum


class SourceType(str, Enum):
    youtube_public = "youtube_public"
    archive_licensed = "archive_licensed"
    partner_private = "partner_private"
    wetransfer = "wetransfer"
    local_file = "local_file"


class AccessTier(str, Enum):
    public = "public"           # Anyone
    registered = "registered"   # Logged-in users
    school = "school"           # School accounts
    pro = "pro"                 # Pro subscribers
    partner = "partner"         # Fashion house partners
    internal = "internal"       # FA team only
