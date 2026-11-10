"""Exercise pgvector in the composed host's native test database."""

from django.db import connection
from django.test import TestCase


class PgvectorDatabaseTests(TestCase):
    def test_untyped_vectors_and_cosine_distance_are_available(self) -> None:
        """The inherited extension supplies variable dimensions and distance SQL."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT vector_dims('[1,0]'::vector), vector_dims('[1,0,0]'::vector), "
                "'[1,0]'::vector <=> '[0,1]'::vector"
            )
            self.assertEqual(cursor.fetchone(), (2, 3, 1.0))
