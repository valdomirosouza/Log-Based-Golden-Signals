from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Analytics API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
