import unittest

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from backend.app.models.knowledge_base import DocumentChunk


class VectorSearchSqlTests(unittest.TestCase):
    def test_cosine_search_compiles_to_pgvector_operator(self) -> None:
        query = select(DocumentChunk).order_by(
            DocumentChunk.embedding.cosine_distance([0.0] * 1536)
        )

        compiled = str(query.compile(dialect=postgresql.dialect()))

        self.assertIn("<=>", compiled)
        self.assertIn("ORDER BY", compiled)


if __name__ == "__main__":
    unittest.main()
