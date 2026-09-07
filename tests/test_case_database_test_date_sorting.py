import gc
import tempfile
import unittest
from pathlib import Path

from core.database import Database


class CaseDatabaseTestDateSortingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.directory.name) / "test.db"))

    def tearDown(self):
        del self.database
        gc.collect()
        self.directory.cleanup()

    def _create_case(self, case_no, test_date):
        return self.database.create_case(
            case_no=case_no,
            patient_name="日期排序测试",
            test_date=test_date,
        )

    def _set_created_at(self, case_no, created_at):
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE cases SET created_at = ? WHERE case_no = ?",
                (created_at, case_no),
            )
            conn.commit()

    @staticmethod
    def _case_numbers(cases):
        return [case["case_no"] for case in cases]

    def test_default_sort_remains_id_descending(self):
        self._create_case("DEFAULT-1", "2026-09-05")
        self._create_case("DEFAULT-2", "2026-09-07")
        self._create_case("DEFAULT-3", "2026-09-06")

        cases = self.database.get_cases()

        self.assertEqual(self._case_numbers(cases), ["DEFAULT-3", "DEFAULT-2", "DEFAULT-1"])

    def test_test_date_descending_places_empty_dates_last_and_uses_id_tiebreaker(self):
        self._create_case("DESC-OLD", "2026-09-05")
        self._create_case("DESC-SAME-1", "2026-09-06")
        self._create_case("DESC-SAME-2", "2026-09-06")
        self._create_case("DESC-NEW", "2026-09-07")
        self._create_case("DESC-EMPTY", "")
        self._create_case("DESC-BLANK", "   ")
        self._create_case("DESC-NULL", None)

        cases = self.database.get_cases(sort_field="test_date", sort_order="desc")

        self.assertEqual(
            self._case_numbers(cases),
            ["DESC-NEW", "DESC-SAME-2", "DESC-SAME-1", "DESC-OLD", "DESC-BLANK", "DESC-EMPTY", "DESC-NULL"],
        )

    def test_test_date_ascending_places_empty_dates_last_and_uses_id_tiebreaker(self):
        self._create_case("ASC-NEW", "2026-09-07")
        self._create_case("ASC-SAME-1", "2026-09-06")
        self._create_case("ASC-SAME-2", "2026-09-06")
        self._create_case("ASC-OLD", "2026-09-05")
        self._create_case("ASC-EMPTY", "")
        self._create_case("ASC-BLANK", "   ")
        self._create_case("ASC-NULL", None)

        cases = self.database.get_cases(sort_field="test_date", sort_order="asc")

        self.assertEqual(
            self._case_numbers(cases),
            ["ASC-OLD", "ASC-SAME-2", "ASC-SAME-1", "ASC-NEW", "ASC-NULL", "ASC-EMPTY", "ASC-BLANK"],
        )

    def test_keyword_search_preserves_requested_test_date_sort(self):
        self._create_case("SEARCH-OLD", "2026-09-05")
        self._create_case("SEARCH-NEW", "2026-09-07")
        self._create_case("OTHER", "2026-09-06")

        descending = self.database.get_cases("SEARCH", "test_date", "desc")
        ascending = self.database.get_cases("SEARCH", "test_date", "asc")

        self.assertEqual(self._case_numbers(descending), ["SEARCH-NEW", "SEARCH-OLD"])
        self.assertEqual(self._case_numbers(ascending), ["SEARCH-OLD", "SEARCH-NEW"])

    def test_created_at_descending_places_empty_times_last_and_uses_id_tiebreaker(self):
        for case_no in (
            "CREATED-DESC-OLD", "CREATED-DESC-SAME-1", "CREATED-DESC-SAME-2",
            "CREATED-DESC-NEW", "CREATED-DESC-EMPTY", "CREATED-DESC-BLANK",
            "CREATED-DESC-NULL",
        ):
            self._create_case(case_no, "2026-09-06")

        self._set_created_at("CREATED-DESC-OLD", "2026-09-05 08:00:00")
        self._set_created_at("CREATED-DESC-SAME-1", "2026-09-06 08:00:00")
        self._set_created_at("CREATED-DESC-SAME-2", "2026-09-06 08:00:00")
        self._set_created_at("CREATED-DESC-NEW", "2026-09-07 08:00:00")
        self._set_created_at("CREATED-DESC-EMPTY", "")
        self._set_created_at("CREATED-DESC-BLANK", "   ")
        self._set_created_at("CREATED-DESC-NULL", None)

        cases = self.database.get_cases(sort_field="created_at", sort_order="desc")

        self.assertEqual(
            self._case_numbers(cases),
            [
                "CREATED-DESC-NEW", "CREATED-DESC-SAME-2", "CREATED-DESC-SAME-1",
                "CREATED-DESC-OLD", "CREATED-DESC-BLANK", "CREATED-DESC-EMPTY",
                "CREATED-DESC-NULL",
            ],
        )

    def test_created_at_ascending_places_empty_times_last_and_uses_id_tiebreaker(self):
        for case_no in (
            "CREATED-ASC-NEW", "CREATED-ASC-SAME-1", "CREATED-ASC-SAME-2",
            "CREATED-ASC-OLD", "CREATED-ASC-EMPTY", "CREATED-ASC-BLANK",
            "CREATED-ASC-NULL",
        ):
            self._create_case(case_no, "2026-09-06")

        self._set_created_at("CREATED-ASC-NEW", "2026-09-07 08:00:00")
        self._set_created_at("CREATED-ASC-SAME-1", "2026-09-06 08:00:00")
        self._set_created_at("CREATED-ASC-SAME-2", "2026-09-06 08:00:00")
        self._set_created_at("CREATED-ASC-OLD", "2026-09-05 08:00:00")
        self._set_created_at("CREATED-ASC-EMPTY", "")
        self._set_created_at("CREATED-ASC-BLANK", "   ")
        self._set_created_at("CREATED-ASC-NULL", None)

        cases = self.database.get_cases(sort_field="created_at", sort_order="asc")

        self.assertEqual(
            self._case_numbers(cases),
            [
                "CREATED-ASC-OLD", "CREATED-ASC-SAME-2", "CREATED-ASC-SAME-1",
                "CREATED-ASC-NEW", "CREATED-ASC-NULL", "CREATED-ASC-EMPTY",
                "CREATED-ASC-BLANK",
            ],
        )

    def test_keyword_search_preserves_requested_created_at_sort(self):
        self._create_case("CREATED-SEARCH-OLD", "2026-09-06")
        self._create_case("CREATED-SEARCH-NEW", "2026-09-06")
        self._create_case("CREATED-OTHER", "2026-09-06")
        self._set_created_at("CREATED-SEARCH-OLD", "2026-09-05 08:00:00")
        self._set_created_at("CREATED-SEARCH-NEW", "2026-09-07 08:00:00")
        self._set_created_at("CREATED-OTHER", "2026-09-06 08:00:00")

        descending = self.database.get_cases("CREATED-SEARCH", "created_at", "desc")
        ascending = self.database.get_cases("CREATED-SEARCH", "created_at", "asc")

        self.assertEqual(self._case_numbers(descending), ["CREATED-SEARCH-NEW", "CREATED-SEARCH-OLD"])
        self.assertEqual(self._case_numbers(ascending), ["CREATED-SEARCH-OLD", "CREATED-SEARCH-NEW"])

    def test_invalid_sort_values_safely_fall_back_to_default_sort(self):
        self._create_case("INVALID-1", "2026-09-05")
        self._create_case("INVALID-2", "2026-09-07")

        invalid_field = self.database.get_cases(sort_field="test_date; DROP TABLE cases", sort_order="desc")
        invalid_order = self.database.get_cases(sort_field="test_date", sort_order="desc; DROP TABLE cases")
        invalid_created_at_order = self.database.get_cases(
            sort_field="created_at", sort_order="desc; DROP TABLE cases"
        )

        self.assertEqual(self._case_numbers(invalid_field), ["INVALID-2", "INVALID-1"])
        self.assertEqual(self._case_numbers(invalid_order), ["INVALID-2", "INVALID-1"])
        self.assertEqual(self._case_numbers(invalid_created_at_order), ["INVALID-2", "INVALID-1"])
        self.assertEqual(len(self.database.get_cases()), 2)
