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

    def test_invalid_sort_values_safely_fall_back_to_default_sort(self):
        self._create_case("INVALID-1", "2026-09-05")
        self._create_case("INVALID-2", "2026-09-07")

        invalid_field = self.database.get_cases(sort_field="test_date; DROP TABLE cases", sort_order="desc")
        invalid_order = self.database.get_cases(sort_field="test_date", sort_order="desc; DROP TABLE cases")

        self.assertEqual(self._case_numbers(invalid_field), ["INVALID-2", "INVALID-1"])
        self.assertEqual(self._case_numbers(invalid_order), ["INVALID-2", "INVALID-1"])
        self.assertEqual(len(self.database.get_cases()), 2)
