from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.database import Database


class TailPublicationDatabaseAtomicTests(unittest.TestCase):
    def _create_case(self, database: Database) -> int:
        with database.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO cases (case_no) VALUES (?)",
                ("CASE-TAIL-PUBLISH",),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def _replace(
        self,
        database: Database,
        case_id: int,
        mean_intensity,
        field_results,
    ) -> int:
        return database.replace_protein_analysis_with_fields(
            case_id=case_id,
            protein_name="Q96P56",
            protein_part="tail",
            image_folder="raw",
            output_folder="formal",
            total_fields=1,
            total_sperm_count=10,
            positive_count=2,
            mean_intensity=mean_intensity,
            expression_rate=20.0,
            field_results=field_results,
            status="完成",
        )

    def test_failed_field_insert_preserves_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "test.db"))
            case_id = self._create_case(database)

            old_id = self._replace(
                database,
                case_id,
                7.0,
                [{
                    "field_no": "1",
                    "sperm_count": 10,
                    "positive_count": 2,
                    "mean_intensity": 7,
                    "expression_rate": 20,
                }],
            )

            with self.assertRaises(Exception):
                self._replace(
                    database,
                    case_id,
                    9.0,
                    [{
                        "field_no": "1",
                        "sperm_count": {"invalid": "sqlite binding"},
                        "positive_count": 2,
                        "mean_intensity": 9,
                        "expression_rate": 20,
                    }],
                )

            rows = database.get_protein_analysis_by_case(case_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], old_id)
            self.assertEqual(float(rows[0]["mean_intensity"]), 7.0)
            fields = database.get_field_results(old_id)
            self.assertEqual(len(fields), 1)
            self.assertEqual(int(fields[0]["sperm_count"]), 10)

    def test_successful_replace_commits_summary_and_fields_together(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "test.db"))
            case_id = self._create_case(database)

            self._replace(
                database,
                case_id,
                7.0,
                [{
                    "field_no": "old",
                    "sperm_count": 10,
                    "positive_count": 2,
                    "mean_intensity": 7,
                    "expression_rate": 20,
                }],
            )
            new_id = self._replace(
                database,
                case_id,
                9.0,
                [
                    {
                        "field_no": "1",
                        "sperm_count": 10,
                        "positive_count": 2,
                        "mean_intensity": 9,
                        "expression_rate": 20,
                    },
                    {
                        "field_no": "2",
                        "sperm_count": 11,
                        "positive_count": 3,
                        "mean_intensity": 10,
                        "expression_rate": 27.27,
                    },
                ],
            )

            rows = database.get_protein_analysis_by_case(case_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], new_id)
            self.assertEqual(float(rows[0]["mean_intensity"]), 9.0)
            fields = database.get_field_results(new_id)
            self.assertEqual(len(fields), 2)
            self.assertEqual(
                [str(item["field_no"]) for item in fields],
                ["1", "2"],
            )


if __name__ == "__main__":
    unittest.main()
