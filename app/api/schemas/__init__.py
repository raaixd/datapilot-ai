"""
Pydantic schemas for VERIDEX REST API.
"""

from app.api.schemas.analyses import (
    AnalysisResultsResponse,
    AnalysisSQLResponse,
    AnalyzeRequest,
    AnalyzeResponse,
)
from app.api.schemas.datasets import (
    DatasetListResponse,
    DatasetProfileResponse,
    DatasetResponse,
    DatasetStatusResponse,
)
from app.api.schemas.legacy import (
    AnalysisPlanModel,
    DataQualityWarningOut,
    FilterOut,
    QueryRequest,
    QueryResponse,
    UploadResponse,
)
from app.api.schemas.projects import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
)
from app.api.schemas.reports import (
    ReportCreateRequest,
    ReportResponse,
)
from app.api.schemas.system import (
    HealthResponse,
    MetricsResponse,
    ReadyResponse,
)

__all__ = [
    # Legacy
    "AnalysisPlanModel",
    "DataQualityWarningOut",
    "FilterOut",
    "QueryRequest",
    "QueryResponse",
    "UploadResponse",
    # System
    "HealthResponse",
    "MetricsResponse",
    "ReadyResponse",
    # Projects
    "ProjectCreate",
    "ProjectListResponse",
    "ProjectResponse",
    # Datasets
    "DatasetListResponse",
    "DatasetProfileResponse",
    "DatasetResponse",
    "DatasetStatusResponse",
    # Analyses
    "AnalysisResultsResponse",
    "AnalysisSQLResponse",
    "AnalyzeRequest",
    "AnalyzeResponse",
    # Reports
    "ReportCreateRequest",
    "ReportResponse",
]
