"""
Tests for SQLAlchemy metadata models, database sessions, and the repository layer.

Exercises all CRUD and lifecycle operations against an isolated SQLite test database.
Tests ensure complete compatibility between the persistence layer, models, and repositories.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.repositories.analysis_repo import AnalysisRepository
from app.db.repositories.dataset_repo import DatasetRepository
from app.db.repositories.evaluation_repo import EvaluationRepository
from app.db.repositories.project_repo import ProjectRepository
from app.db.repositories.report_repo import ReportRepository
from app.db.session import session_scope


@pytest.fixture
def test_db():
    """Create a fresh in-memory SQLite database for repository tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


class TestProjectRepository:
    def test_create_and_get_project(self, test_db):
        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            project = repo.create_project(name="Sales Analytics Q3", description="Quarterly sales data")
            assert project.id is not None
            assert project.name == "Sales Analytics Q3"
            assert project.description == "Quarterly sales data"
            proj_id = project.id

        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            fetched = repo.get_project(proj_id)
            assert fetched is not None
            assert fetched.id == proj_id
            assert fetched.name == "Sales Analytics Q3"

    def test_list_projects(self, test_db):
        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            repo.create_project(name="Project Alpha")
            repo.create_project(name="Project Beta")

        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            projects = repo.list_projects()
            assert len(projects) == 2
            names = [p.name for p in projects]
            assert "Project Alpha" in names
            assert "Project Beta" in names

    def test_update_project(self, test_db):
        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            p = repo.create_project(name="Initial Name")
            p_id = p.id

        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            updated = repo.update_project(p_id, name="Updated Name", description="Added description")
            assert updated.name == "Updated Name"
            assert updated.description == "Added description"

        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            fetched = repo.get_project(p_id)
            assert fetched.name == "Updated Name"

    def test_delete_project(self, test_db):
        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            p = repo.create_project(name="To Delete")
            p_id = p.id

        with session_scope(test_db) as session:
            repo = ProjectRepository(session)
            deleted = repo.delete_project(p_id)
            assert deleted is True
            assert repo.get_project(p_id) is None


class TestDatasetRepository:
    def test_create_and_lifecycle_transitions(self, test_db):
        with session_scope(test_db) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Data Project")
            proj_id = proj.id

            d_repo = DatasetRepository(session)
            dataset = d_repo.create_dataset(
                project_id=proj_id,
                filename="customers.csv",
                file_type="csv",
                s3_key="raw/proj/cust/customers.csv",
                file_size=1024,
                checksum="sha256_dummy_hash",
            )
            assert dataset.processing_status == "UPLOADED"
            dataset_id = dataset.id

        # Transition to VALIDATING
        with session_scope(test_db) as session:
            d_repo = DatasetRepository(session)
            updated = d_repo.update_status(dataset_id, "VALIDATING")
            assert updated.processing_status == "VALIDATING"

        # Transition to PROCESSING
        with session_scope(test_db) as session:
            d_repo = DatasetRepository(session)
            updated = d_repo.update_status(dataset_id, "PROCESSING")
            assert updated.processing_status == "PROCESSING"

        # Transition to READY with row and column counts
        with session_scope(test_db) as session:
            d_repo = DatasetRepository(session)
            updated = d_repo.update_status(dataset_id, "READY", row_count=5000, column_count=12)
            assert updated.processing_status == "READY"
            assert updated.row_count == 5000
            assert updated.column_count == 12

    def test_idempotency_by_checksum(self, test_db):
        checksum_val = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        with session_scope(test_db) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Idempotency Test")
            d_repo = DatasetRepository(session)
            d_repo.create_dataset(
                project_id=proj.id,
                filename="upload.csv",
                file_type="csv",
                s3_key="raw/upload.csv",
                checksum=checksum_val,
            )

        with session_scope(test_db) as session:
            d_repo = DatasetRepository(session)
            existing = d_repo.get_by_checksum(proj.id, checksum_val)
            assert existing is not None
            assert existing.checksum == checksum_val

            missing = d_repo.get_by_checksum(proj.id, "different_hash")
            assert missing is None

    def test_set_and_get_columns(self, test_db):
        with session_scope(test_db) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Schema Test")
            d_repo = DatasetRepository(session)
            dataset = d_repo.create_dataset(
                project_id=proj.id,
                filename="sales.csv",
                file_type="csv",
                s3_key="raw/sales.csv",
                checksum="abc123hash",
            )
            dataset_id = dataset.id

            columns = [
                {
                    "name": "revenue",
                    "physical_type": "float64",
                    "semantic_type": "currency",
                    "nullable": False,
                    "description": "Total sales revenue in USD",
                    "statistics": {"min": 10.0, "max": 1500.0, "mean": 245.5},
                },
                {
                    "name": "region",
                    "physical_type": "object",
                    "semantic_type": "categorical",
                    "nullable": True,
                    "description": "Sales territory",
                    "statistics": {"cardinality": 4, "top_values": ["North", "South"]},
                },
            ]
            d_repo.set_columns(dataset_id, columns)

        with session_scope(test_db) as session:
            d_repo = DatasetRepository(session)
            cols = d_repo.get_columns(dataset_id)
            assert len(cols) == 2
            rev_col = next(c for c in cols if c.name == "revenue")
            assert rev_col.semantic_type == "currency"
            assert rev_col.physical_type == "float64"
            assert rev_col.nullable is False
            assert rev_col.statistics_json["mean"] == 245.5


class TestAnalysisRepository:
    def test_analysis_run_and_query_tracking(self, test_db):
        with session_scope(test_db) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Analysis Project")
            d_repo = DatasetRepository(session)
            dataset = d_repo.create_dataset(
                project_id=proj.id,
                filename="data.csv",
                file_type="csv",
                s3_key="raw/data.csv",
                checksum="hash1",
            )
            dataset_id = dataset.id

            a_repo = AnalysisRepository(session)
            run = a_repo.create_analysis_run(
                dataset_id=dataset_id,
                question="What was our total revenue in 2024?",
                scope="in_scope",
                llm_provider="gemini",
            )
            assert run.status == "PENDING"
            run_id = run.id

            # Record failed initial query + repair history
            history = [{"attempt": 1, "sql": "SELECT rev FROM data", "error": "no such column: rev"}]
            a_repo.record_query(
                analysis_run_id=run_id,
                sql="SELECT SUM(revenue) FROM data",
                execution_time_ms=45,
                repair_count=1,
                status="SUCCESS",
                correction_history=history,
            )

            # Record result
            a_repo.record_result(
                analysis_run_id=run_id,
                row_count=1,
                preview=[{"sum(revenue)": 1250000.0}],
                metrics={"total_revenue": 1250000.0},
                insight_text="Total revenue in 2024 was $1,250,000.",
                chart_type="bar",
            )

            # Record insight
            a_repo.record_insight(
                analysis_run_id=run_id,
                dataset_id=dataset_id,
                title="2024 Revenue Milestone",
                summary="Revenue reached $1.25M with consistent monthly volume.",
                findings=["Peak sales occurred in Q4", "Average order value rose 12%"],
                sql_used="SELECT SUM(revenue) FROM data",
            )

            # Complete run
            a_repo.complete_analysis_run(run_id, latency_ms=1420, status="COMPLETED")

        # Verify all recorded records
        with session_scope(test_db) as session:
            a_repo = AnalysisRepository(session)
            run = a_repo.get_analysis_run(run_id)
            assert run is not None
            assert run.status == "COMPLETED"
            assert run.latency_ms == 1420
            assert run.completed_at is not None

            queries = a_repo.get_queries(run_id)
            assert len(queries) == 1
            assert queries[0].repair_count == 1
            assert len(queries[0].correction_history_json) == 1

            result = a_repo.get_result(run_id)
            assert result is not None
            assert result.row_count == 1
            assert result.metrics_json["total_revenue"] == 1250000.0

            insights = a_repo.list_insights(dataset_id=dataset_id)
            assert len(insights) == 1
            assert insights[0].title == "2024 Revenue Milestone"
            assert len(insights[0].findings_json) == 2


class TestReportRepository:
    def test_create_and_retrieve_report(self, test_db):
        with session_scope(test_db) as session:
            p_repo = ProjectRepository(session)
            proj = p_repo.create_project(name="Report Project")
            d_repo = DatasetRepository(session)
            dataset = d_repo.create_dataset(
                project_id=proj.id,
                filename="metrics.csv",
                file_type="csv",
                s3_key="raw/metrics.csv",
                checksum="reporthash",
            )

            r_repo = ReportRepository(session)
            report = r_repo.create_report(
                project_id=proj.id,
                dataset_id=dataset.id,
                title="Executive Sales Summary Q3",
                summary="Comprehensive analysis of Q3 revenue patterns.",
                content={"sections": ["Overview", "Key Findings", "Anomalies"]},
                s3_key="reports/proj/rep1/report.json",
            )
            report_id = report.id

        with session_scope(test_db) as session:
            r_repo = ReportRepository(session)
            fetched = r_repo.get_report(report_id)
            assert fetched is not None
            assert fetched.title == "Executive Sales Summary Q3"
            assert fetched.content_json["sections"][1] == "Key Findings"
            assert fetched.s3_key == "reports/proj/rep1/report.json"


class TestEvaluationRepository:
    def test_record_and_list_eval_runs(self, test_db):
        with session_scope(test_db) as session:
            e_repo = EvaluationRepository(session)
            eval_run = e_repo.record_evaluation_run(
                dataset_name="ecommerce.csv",
                total_cases=100,
                sql_accuracy=0.94,
                execution_success=0.97,
                answer_correctness=0.95,
                groundedness=0.98,
                avg_latency_ms=2100.0,
                repair_rate=0.08,
                unresolved_rate=0.03,
                details={"categories": {"aggregation": 1.0, "ranking": 0.95}},
            )
            run_id = eval_run.id

        with session_scope(test_db) as session:
            e_repo = EvaluationRepository(session)
            fetched = e_repo.get_evaluation_run(run_id)
            assert fetched is not None
            assert fetched.total_cases == 100
            assert fetched.sql_accuracy == 0.94
            assert fetched.details_json["categories"]["aggregation"] == 1.0

            runs = e_repo.list_evaluation_runs()
            assert len(runs) >= 1
