import json
from datetime import datetime
from typing import Any

import pandas as pd

from src.db.manager import DatabaseManager

WORK_COLUMNS = [
    "work_key",
    "title",
    "first_publish_year",
    "edition_count",
    "languages",
    "isbn",
    "subjects",
    "source",
    "created_at",
    "updated_at",
]
AUTHOR_COLUMNS = ["author_key", "name"]
LINK_COLUMNS = ["work_key", "author_key"]
YEAR = "first_publish_year"


def _ensure_columns(frame: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(columns=columns) if frame is None else frame.copy()
    for column in columns:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame


def _python_value(value: Any) -> Any:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


class DataProcessor:
    def __init__(
        self,
        works: pd.DataFrame | None,
        authors: pd.DataFrame | None = None,
        work_authors: pd.DataFrame | None = None,
    ) -> None:
        self.works = self._prepare_works(_ensure_columns(works, WORK_COLUMNS))
        self.authors = _ensure_columns(authors, AUTHOR_COLUMNS)[AUTHOR_COLUMNS]
        self.work_authors = _ensure_columns(work_authors, LINK_COLUMNS)[LINK_COLUMNS]
        self.authors["name"] = self.authors["name"].fillna("Unknown").astype(str).str.strip()

    @classmethod
    def from_database(cls, db: DatabaseManager) -> "DataProcessor":
        return cls(
            pd.DataFrame(db.fetch_table("works")),
            pd.DataFrame(db.fetch_table("authors")),
            pd.DataFrame(db.fetch_table("work_authors")),
        )

    @property
    def is_empty(self) -> bool:
        return self.works.empty

    @staticmethod
    def _prepare_works(df: pd.DataFrame) -> pd.DataFrame:
        df = df.drop_duplicates(subset="work_key", keep="last").reset_index(drop=True)
        df["title"] = df["title"].fillna("Untitled").astype(str).str.strip()
        df["title"] = df["title"].replace("", "Untitled")

        years = pd.to_numeric(df[YEAR], errors="coerce").astype("Int64")
        valid = years.between(1, datetime.now().year + 1).fillna(False).astype(bool)
        df[YEAR] = years.where(valid)
        df["decade"] = (df[YEAR] // 10 * 10).astype("Int64")

        editions = pd.to_numeric(df["edition_count"], errors="coerce")
        df["edition_count"] = editions.fillna(0).clip(lower=0).astype(int)
        for column in ("languages", "subjects", "source"):
            df[column] = df[column].fillna("").astype(str)
        df["isbn"] = df["isbn"].replace("", pd.NA)
        for column in ("created_at", "updated_at"):
            df[column] = pd.to_datetime(df[column], errors="coerce", utc=True)
        return df

    def merged(self) -> pd.DataFrame:
        merged = self.works.merge(self.work_authors, on="work_key", how="left").merge(
            self.authors, on="author_key", how="left"
        )
        merged["name"] = merged["name"].fillna("Unknown")
        return merged

    def _exploded_counts(self, column: str, separator: str, label: str, n: int) -> pd.DataFrame:
        values = self.works[["work_key", column]].copy()
        values[label] = values[column].str.split(separator)
        values = values.explode(label)
        values[label] = values[label].fillna("").str.strip()
        values = values[values[label] != ""].drop_duplicates(["work_key", label])
        if values.empty:
            return pd.DataFrame(columns=[label, "works", "share_pct"])
        counts = values.groupby(label).size().reset_index(name="works")
        counts["share_pct"] = (counts["works"] / values["work_key"].nunique() * 100).round(2)
        return counts.sort_values(["works", label], ascending=[False, True]).head(n)

    def top_publication_years(self, n: int = 10) -> pd.DataFrame:
        dated = self.works.dropna(subset=[YEAR])
        if dated.empty:
            return pd.DataFrame(columns=[YEAR, "works", "total_editions", "share_pct"])
        grouped = (
            dated.groupby(YEAR)
            .agg(works=("work_key", "count"), total_editions=("edition_count", "sum"))
            .reset_index()
        )
        grouped["share_pct"] = (grouped["works"] / len(dated) * 100).round(2)
        return grouped.sort_values(["works", YEAR], ascending=[False, True]).head(n)

    def top_languages(self, n: int = 10) -> pd.DataFrame:
        return self._exploded_counts("languages", ",", "language", n)

    def top_subjects(self, n: int = 15) -> pd.DataFrame:
        return self._exploded_counts("subjects", "|", "subject", n)

    def works_by_decade(self) -> pd.DataFrame:
        dated = self.works.dropna(subset=["decade"])
        if dated.empty:
            return pd.DataFrame(columns=["decade", "works", "avg_editions"])
        return (
            dated.groupby("decade")
            .agg(works=("work_key", "count"), avg_editions=("edition_count", "mean"))
            .round({"avg_editions": 2})
            .reset_index()
            .sort_values("decade")
        )

    def top_authors(self, n: int = 10) -> pd.DataFrame:
        merged = self.merged()
        merged = merged[merged["author_key"].notna()]
        if merged.empty:
            return pd.DataFrame(
                columns=["author", "works", "total_editions", "first_year", "latest_year"]
            )
        grouped = (
            merged.groupby("name")
            .agg(
                works=("work_key", "nunique"),
                total_editions=("edition_count", "sum"),
                first_year=(YEAR, "min"),
                latest_year=(YEAR, "max"),
            )
            .reset_index()
            .rename(columns={"name": "author"})
        )
        return grouped.sort_values(["works", "total_editions"], ascending=False).head(n)

    def works_table(self) -> pd.DataFrame:
        names = (
            self.merged()
            .loc[lambda df: df["author_key"].notna()]
            .groupby("work_key")["name"]
            .agg(lambda items: ", ".join(sorted(set(items))))
            .rename("authors")
        )
        table = self.works.merge(names, on="work_key", how="left")
        table["authors"] = table["authors"].fillna("")
        table["updated_at"] = table["updated_at"].dt.strftime("%Y-%m-%d %H:%M")
        columns = [
            "work_key",
            "title",
            "authors",
            YEAR,
            "edition_count",
            "languages",
            "isbn",
            "source",
            "updated_at",
        ]
        return table[columns].sort_values(["edition_count", "title"], ascending=[False, True])

    def summary(self, n: int = 10) -> pd.DataFrame:
        years = self.top_publication_years(n).rename(columns={YEAR: "value"})
        years = years.assign(dimension="publication_year")
        languages = self.top_languages(n).rename(columns={"language": "value"})
        languages = languages.assign(dimension="language")
        columns = ["dimension", "value", "works", "share_pct"]
        frames = [frame[columns] for frame in (years, languages) if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=columns)
        return pd.concat(frames, ignore_index=True)

    def kpis(self) -> dict[str, Any]:
        works = self.works
        if works.empty:
            return {"total_works": 0}
        years = works[YEAR].dropna()
        top = {
            "top_language": self.top_languages(1),
            "top_subject": self.top_subjects(1),
            "top_author": self.top_authors(1),
        }
        first = {
            "top_language": "language",
            "top_subject": "subject",
            "top_author": "author",
        }
        metrics: dict[str, Any] = {
            "total_works": len(works),
            "total_authors": int(self.work_authors["author_key"].nunique()),
            "total_editions": int(works["edition_count"].sum()),
            "avg_editions_per_work": round(float(works["edition_count"].mean()), 2),
            "median_publish_year": int(years.median()) if not years.empty else None,
            "oldest_publish_year": int(years.min()) if not years.empty else None,
            "newest_publish_year": int(years.max()) if not years.empty else None,
            "isbn_coverage_pct": round(float(works["isbn"].notna().mean() * 100), 2),
            "distinct_languages": len(self.top_languages(10_000)),
        }
        for key, frame in top.items():
            metrics[key] = None if frame.empty else str(frame.iloc[0][first[key]])
        return {key: _python_value(value) for key, value in metrics.items()}

    @staticmethod
    def to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        return json.loads(frame.to_json(orient="records", date_format="iso"))
