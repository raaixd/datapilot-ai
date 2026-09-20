"""
Repository for Project entities.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Project
from app.db.repositories.base import BaseRepository


class ProjectRepository(BaseRepository):
    """Data access operations for projects."""

    def create_project(self, name: str, description: str | None = None) -> Project:
        project = Project(name=name.strip(), description=description.strip() if description else None)
        self.session.add(project)
        self.session.flush()
        return project

    def get_project(self, project_id: str) -> Project | None:
        stmt = select(Project).where(Project.id == project_id)
        return self.session.scalars(stmt).first()

    def get_by_name(self, name: str) -> Project | None:
        stmt = select(Project).where(Project.name == name.strip())
        return self.session.scalars(stmt).first()

    def list_projects(self, limit: int = 100, offset: int = 0) -> list[Project]:
        stmt = select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def update_project(self, project_id: str, name: str | None = None, description: str | None = None) -> Project | None:
        project = self.get_project(project_id)
        if not project:
            return None
        if name is not None:
            project.name = name.strip()
        if description is not None:
            project.description = description.strip()
        self.session.flush()
        return project

    def delete_project(self, project_id: str) -> bool:
        project = self.get_project(project_id)
        if not project:
            return False
        self.session.delete(project)
        self.session.flush()
        return True
