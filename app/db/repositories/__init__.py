"""
Repository package providing clean data-access abstractions over SQLAlchemy models.
"""

from app.db.repositories.analysis_repo import AnalysisRepository
from app.db.repositories.dataset_repo import DatasetRepository
from app.db.repositories.evaluation_repo import EvaluationRepository
from app.db.repositories.project_repo import ProjectRepository
from app.db.repositories.report_repo import ReportRepository

__all__ = [
    "AnalysisRepository",
    "DatasetRepository",
    "EvaluationRepository",
    "ProjectRepository",
    "ReportRepository",
]
