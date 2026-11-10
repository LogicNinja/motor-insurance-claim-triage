"""Retrieval service. Owns the embedding model and the Chroma connection.

Stage 1: implement /search (embed the query, query Chroma with metadata filters).
Stage 4: implement /clause/{id} (exact lookup) and /references (resolve a named reference such as
'Limitations as to Use' to the clause that defines it).
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Retrieval service", version="0.1.0")


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    filters: dict = {}


class ReferenceRequest(BaseModel):
    term: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
def search(req: SearchRequest):
    raise HTTPException(status_code=501, detail="Not implemented: Stage 1")


@app.get("/clause/{clause_id}")
def clause(clause_id: str):
    raise HTTPException(status_code=501, detail="Not implemented: Stage 4")


@app.post("/references")
def references(req: ReferenceRequest):
    raise HTTPException(status_code=501, detail="Not implemented: Stage 4")
