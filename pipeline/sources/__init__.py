"""Job sources. Each module has a fetch_jobs(token) function returning raw job dicts."""

from pipeline.sources import greenhouse, lever

SOURCES = {
    "greenhouse": greenhouse,
    "lever": lever,
}
