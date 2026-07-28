"""Aggregates every v1 router."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    books,
    library,
    playlists,
    reading,
    recommendations,
    users,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(books.router, prefix="/books", tags=["Books"])
api_router.include_router(
    recommendations.router, prefix="/recommendations", tags=["Recommendations"]
)
api_router.include_router(reading.router, prefix="/reading", tags=["Reading"])
api_router.include_router(playlists.router, prefix="/playlists", tags=["Playlists"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(library.router, prefix="/library", tags=["Library"])
