"""Source registry — add new sources to _REGISTRY as they're implemented."""
from .base import Article, ArticleRef, Source, fetch_image_bytes, make_scraper_session
from .p_articles import PArticlesSource

_REGISTRY: dict[str, type[Source]] = {
    PArticlesSource.name: PArticlesSource,
}


def get_source(name: str) -> Source:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown source {name!r}. Known: {sorted(_REGISTRY)}")
    return cls()


def known_sources() -> list[str]:
    return sorted(_REGISTRY.keys())


__all__ = [
    "Article", "ArticleRef", "Source",
    "fetch_image_bytes", "make_scraper_session",
    "get_source", "known_sources",
]
