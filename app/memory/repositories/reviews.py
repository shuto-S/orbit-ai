import sqlite3
from collections.abc import Callable

from app.memory.models import DailyReview
from app.memory.utils import loads_review_items, now_iso
from app.text import sanitize_text


class DailyReviewRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_daily_review(self, review_date: str, summary: str, items_json: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO daily_reviews (review_date, summary, items_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    sanitize_text(review_date),
                    sanitize_text(summary),
                    sanitize_text(items_json),
                    now_iso(),
                ),
            )
        return int(cursor.lastrowid)

    def recent_daily_reviews(self, limit: int = 5) -> list[DailyReview]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, review_date, summary, items_json, created_at
                FROM daily_reviews
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            DailyReview(
                id=row["id"],
                review_date=row["review_date"],
                summary=row["summary"],
                items=loads_review_items(row["items_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
