"""FastAPI interface to MergeMind's shared resolution pipeline."""

from fastapi import FastAPI, HTTPException

from schemas.models import ConflictInput, FinalMergeResult
from tools.resolution_pipeline import resolve_conflict

app = FastAPI(title="MergeMind API", description="AI-powered merge conflict resolution")


@app.get("/")
def read_root() -> dict[str, str]:
    """Report that the MergeMind API process is available."""
    return {"message": "MergeMind Backend is running"}


@app.post("/api/resolve", response_model=FinalMergeResult)
def resolve_conflict_endpoint(conflict: ConflictInput) -> FinalMergeResult:
    """Resolve the supplied three-way conflict using the shared core pipeline."""
    try:
        return resolve_conflict(conflict)
    except Exception as exc:
        # Unexpected integration failures should be visible to clients instead
        # of being disguised as a successful API response.
        raise HTTPException(status_code=500, detail=f"Resolution pipeline failed: {exc}") from exc
