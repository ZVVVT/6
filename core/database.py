import sqlite3
from pathlib import Path
from datetime import datetime


class Database:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self):
        return sqlite3.connect(str(self.db_path))

    def init_db(self):
        with self.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_no TEXT UNIQUE NOT NULL,
                patient_name TEXT,
                age INTEGER,
                sample_no TEXT,
                test_date TEXT,
                remark TEXT,
                created_at TEXT,
                updated_at TEXT,
                report_path TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS protein_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                protein_name TEXT,
                protein_part TEXT,
                image_folder TEXT,
                output_folder TEXT,
                total_fields INTEGER DEFAULT 0,
                total_sperm_count INTEGER DEFAULT 0,
                positive_count INTEGER DEFAULT 0,
                mean_intensity REAL DEFAULT 0,
                expression_rate REAL DEFAULT 0,
                status TEXT,
                created_at TEXT,
                FOREIGN KEY(case_id) REFERENCES cases(id)
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS field_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER NOT NULL,
                field_no TEXT,
                sperm_count INTEGER DEFAULT 0,
                positive_count INTEGER DEFAULT 0,
                mean_intensity REAL DEFAULT 0,
                expression_rate REAL DEFAULT 0,
                overlay_image_path TEXT,
                csv_path TEXT,
                FOREIGN KEY(analysis_id) REFERENCES protein_analysis(id)
            )
            """)

            conn.commit()

    def create_case(self, case_no, patient_name, age, sample_no, test_date, remark):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO cases (
                case_no, patient_name, age, sample_no, test_date,
                remark, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case_no, patient_name, age, sample_no, test_date,
                remark, now, now
            ))
            conn.commit()
            return cursor.lastrowid

    def get_cases(self):
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id, case_no, patient_name, age, sample_no, test_date, created_at, report_path
            FROM cases
            ORDER BY id DESC
            """)
            return cursor.fetchall()