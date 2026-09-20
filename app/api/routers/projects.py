"""
Projects API router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas.projects import ProjectCreate, ProjectListResponse, ProjectResponse
from app.db.repositories.project_repo import ProjectRepository
from app.db.session import get_db

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectResponse:
    repo = ProjectRepository(db)
    existing = repo.get_by_name(payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with name '{payload.name}' already exists.",
        )
    project = repo.create_project(name=payload.name, description=payload.description)
    db.commit()
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


@router.get("", response_model=ProjectListResponse)
def list_projects(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)) -> ProjectListResponse:
    repo = ProjectRepository(db)
    projects = repo.list_projects(limit=limit, offset=offset)
    return ProjectListResponse(
        projects=[
            ProjectResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                created_at=p.created_at.isoformat(),
                updated_at=p.updated_at.isoformat(),
            )
            for p in projects
        ],
        total=len(projects),
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectResponse:
    repo = ProjectRepository(db)
    project = repo.get_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found.")
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )
