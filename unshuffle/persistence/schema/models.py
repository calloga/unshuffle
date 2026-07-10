from peewee import *
from datetime import datetime

db_proxy = DatabaseProxy()

class BaseModel(Model):
    """All models inherit this to share the database connection."""


BaseModel._meta.database = db_proxy


# ============================================================================
# CORE TABLES (001_initial_core_tables.sql)
# ============================================================================

class SchemaVersion(BaseModel):
    """Schema version tracking."""
    id = IntegerField(primary_key=True)
    version = IntegerField(default=0)

    class Meta:
        table_name = 'schema_version'


class FileCache(BaseModel):
    """Cache for file characteristics and feature vectors."""
    hash = TextField(primary_key=True)
    fast_hash = TextField(null=True)
    last_path = TextField(null=True)
    size = IntegerField(null=True)
    mtime = FloatField(null=True)
    first_seen = DateTimeField(default=datetime.now)
    feature_vector = BlobField(null=True)
    feature_space_version = TextField(null=True)
    extractor_version = TextField(null=True)
    feature_schema_json = TextField(null=True)
    analysis_status = TextField(null=True)
    analysis_tags_json = TextField(null=True)
    updated_at = DateTimeField(null=True)

    class Meta:
        table_name = 'file_cache'
        indexes = (
            (('hash',), False),
            (('last_path',), False),
            (('last_path', 'size', 'mtime'), False),
        )


class Session(BaseModel):
    """Session records for unshuffle operations."""
    session_id = TextField(primary_key=True)
    timestamp = DateTimeField(default=datetime.now)
    source_path = TextField()
    target_root = TextField()
    mode = TextField()
    is_flat = BooleanField()

    class Meta:
        table_name = 'sessions'


class SessionSource(BaseModel):
    """Source paths associated with a session."""
    session_id = TextField()
    source_path = TextField()
    ordinal = IntegerField(default=0)

    class Meta:
        table_name = 'session_sources'
        primary_key = CompositeKey('session_id', 'source_path')
        indexes = (
            (('session_id',), False),
        )


class SessionMetadata(BaseModel):
    """Metadata key-value pairs for sessions."""
    session_id = TextField()
    key = TextField()
    value_json = TextField(null=True)

    class Meta:
        table_name = 'session_metadata'
        primary_key = CompositeKey('session_id', 'key')
        indexes = (
            (('session_id',), False),
        )


class Record(BaseModel):
    """File records for a session."""
    id = IntegerField(primary_key=True)
    session_id = TextField()
    source_path = TextField()
    target_path = TextField()
    category = TextField(null=True)
    subcategory = TextField(null=True)
    pack = TextField(null=True)
    file_hash = TextField(null=True)
    fast_hash = TextField(null=True)
    confidence = FloatField(null=True)
    status = TextField(null=True)
    tags = TextField(null=True)
    step_status = TextField(default='PENDING')
    original_action = TextField(null=True)
    trash_path = TextField(null=True)
    preserved_root = TextField(null=True)
    is_preserved = IntegerField(default=0)

    class Meta:
        table_name = 'records'
        indexes = (
            (('session_id',), False),
            (('status', 'file_hash'), False),
        )


class TokenAdjustment(BaseModel):
    """Token weight adjustments for categorization."""
    token = TextField()
    category = TextField()
    weight_offset = FloatField(default=0.0)

    class Meta:
        table_name = 'token_adjustments'
        primary_key = CompositeKey('token', 'category')


class LearnedCorrectionEvent(BaseModel):
    """Learned correction events for category reclassification."""
    source_key = TextField()
    token = TextField()
    old_category = TextField()
    new_category = TextField()
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'learned_correction_events'
        primary_key = CompositeKey('source_key', 'token', 'old_category', 'new_category')


class Alias(BaseModel):
    """Category aliases."""
    alias_value = TextField(column_name='alias', primary_key=True)
    category = TextField()
    weight = FloatField()
    source = TextField(default='system')

    class Meta:
        table_name = 'aliases'


class ConfigList(BaseModel):
    """Configuration lists (user lists, system lists, etc.)."""
    list_type = TextField()
    value = TextField()

    class Meta:
        table_name = 'config_lists'
        primary_key = CompositeKey('list_type', 'value')


class Exclusion(BaseModel):
    """Exclusion paths."""
    path = TextField(primary_key=True)
    timestamp = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'exclusions'


class SuppressionRule(BaseModel):
    """Suppression rules for categories."""
    suppressor = TextField()
    target = TextField()

    class Meta:
        table_name = 'suppression_rules'
        primary_key = CompositeKey('suppressor', 'target')


class SubTaxonomy(BaseModel):
    """Sub-taxonomy mappings."""
    category = TextField()
    token = TextField()
    sub_category = TextField()

    class Meta:
        table_name = 'sub_taxonomy'
        primary_key = CompositeKey('category', 'token')


class StagingRecord(BaseModel):
    """Staging area for file records during processing."""
    id = IntegerField(primary_key=True)
    row_id = IntegerField(null=True)
    session_id = TextField()
    source_path = TextField()
    sample_name = TextField(null=True)
    pack = TextField(null=True)
    category = TextField(null=True)
    subcategory = TextField(null=True)
    audio_type = TextField(null=True)
    tags = TextField(null=True)
    confidence = TextField(null=True)
    duration = FloatField(null=True)
    hash = TextField(null=True)
    fast_hash = TextField(null=True)
    pack_candidates = TextField(null=True)
    evidence_json = TextField(null=True)
    feature_vector = BlobField(null=True)
    feature_space_version = TextField(null=True)
    feature_schema_json = TextField(null=True)
    analysis_status = TextField(null=True)
    analysis_tags_json = TextField(null=True)
    preserved_root = TextField(null=True)
    is_preserved = IntegerField(default=0)

    class Meta:
        table_name = 'staging_records'
        indexes = (
            (('session_id',), False),
            (('session_id', 'row_id', 'id'), False),
            (('session_id', 'source_path'), False),
            (('session_id', 'audio_type'), False),
            (('session_id', 'category'), False),
            (('session_id', 'subcategory'), False),
            (('session_id', 'pack'), False),
            (('session_id', 'confidence'), False),
        )


class CustomTreeMembership(BaseModel):
    """Derived DB-backed route membership for an active custom tree."""

    session_id = TextField()
    profile_id = TextField()
    projection_signature = TextField()
    route_key = TextField()
    parent_route_key = TextField()
    label = TextField()
    node_type = TextField()
    semantic_fields_json = TextField(default="{}")
    source_node_id = TextField(null=True)
    source_node_type = TextField(null=True)
    read_only = IntegerField(default=0)
    residual = IntegerField(default=0)
    sort_order = IntegerField(default=0)
    depth = IntegerField()
    row_id = IntegerField()

    class Meta:
        table_name = "custom_tree_memberships"
        primary_key = CompositeKey(
            "session_id",
            "profile_id",
            "projection_signature",
            "route_key",
            "row_id",
        )


# ============================================================================
# COHERENCE TABLES (002_initial_coherence_tables.sql)
# ============================================================================

class CoherenceResult(BaseModel):
    """Coherence analysis results for records."""
    session_id = TextField()
    record_id = TextField()
    category = TextField(null=True)
    subcategory = TextField(null=True)
    coherence_status = TextField(null=True)
    coherence_score = FloatField(null=True)
    cluster_id = TextField(null=True)
    is_outlier = IntegerField(default=0)
    review_reason = TextField(null=True)
    suggested_alternate_category = TextField(null=True)
    suggested_alternate_subcategory = TextField(null=True)
    nearest_neighbor_summary_json = TextField(null=True)
    anchor_fit_status = TextField(null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'coherence_results'
        primary_key = CompositeKey('session_id', 'record_id')
        indexes = (
            (('session_id',), False),
        )


class RefinementCandidate(BaseModel):
    """Candidates for refinement during coherence review."""
    session_id = TextField()
    candidate_id = TextField()
    record_id = TextField()
    current_audio_type = TextField(null=True)
    current_category = TextField(null=True)
    current_subcategory = TextField(null=True)
    suggested_audio_type = TextField(null=True)
    suggested_category = TextField(null=True)
    suggested_subcategory = TextField(null=True)
    evidence = TextField(null=True)
    coherence_status = TextField(null=True)
    confidence_score = FloatField(null=True)
    state = TextField(default='pending')
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'refinement_candidates'
        primary_key = CompositeKey('session_id', 'candidate_id')
        indexes = (
            (('session_id', 'state'), False),
        )


class AnchorProfile(BaseModel):
    """Anchor profiles for coherence checking."""
    session_id = TextField()
    anchor_id = TextField()
    audio_type = TextField(null=True)
    category = TextField(null=True)
    subcategory = TextField(null=True)
    cluster_id = TextField(null=True)
    feature_space_version = TextField(null=True)
    extractor_version = TextField(null=True)
    feature_schema_json = TextField(null=True)
    medoid_vector = BlobField(null=True)
    cluster_centroid = BlobField(null=True)
    cluster_std = BlobField(null=True)
    coherence_radius = FloatField(null=True)
    n_reference_items = IntegerField(null=True)
    state = TextField(default='candidate')
    profile_json = TextField(null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'anchor_profiles'
        primary_key = CompositeKey('session_id', 'anchor_id')
        indexes = (
            (('session_id', 'state'), False),
        )


class CoherenceReviewDecision(BaseModel):
    """Decisions made during coherence review."""
    source_path = TextField(primary_key=True)
    file_hash = TextField(null=True)
    decision_type = TextField()
    current_audio_type = TextField(null=True)
    current_category = TextField(null=True)
    current_subcategory = TextField(null=True)
    target_audio_type = TextField(null=True)
    target_category = TextField(null=True)
    target_subcategory = TextField(null=True)
    created_session_id = TextField(null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'coherence_review_decisions'
        indexes = (
            (('file_hash',), False),
        )

