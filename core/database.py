import sqlite3
from pathlib import Path
from datetime import datetime


class Database:
    CASE_FIELD_DEFS = {
        "patient_name": "TEXT",
        "age": "INTEGER",
        "sex": "TEXT",
        "occupation": "TEXT",
        "phone": "TEXT",
        "sample_no": "TEXT",
        "test_date": "TEXT",
        "remark": "TEXT",

        "protein_analysis_enabled": "INTEGER DEFAULT 1",

        "collect_time": "TEXT",
        "receive_time": "TEXT",
        "semen_volume": "TEXT",
        "ph_value": "TEXT",
        "appearance": "TEXT",
        "color": "TEXT",
        "liquefaction_time": "TEXT",
        "liquefaction_status": "TEXT",
        "agglutination": "TEXT",
        "viscosity": "TEXT",
        "collect_method": "TEXT",
        "abstinence_days": "TEXT",
        "smell": "TEXT",
        "test_temperature": "TEXT",
        "collect_location": "TEXT",
        "collect_complete": "TEXT",
        "dead_sperm": "TEXT",

        "sperm_concentration": "TEXT",
        "sperm_total": "TEXT",
        "forward_motility": "TEXT",
        "total_motility": "TEXT",

        "checker": "TEXT",
        "reviewer": "TEXT",
        "doctor": "TEXT",
        "department": "TEXT",

        "conclusion_normal": "INTEGER DEFAULT 0",
        "conclusion_oligo": "INTEGER DEFAULT 0",
        "conclusion_astheno": "INTEGER DEFAULT 0",
        "conclusion_oligoastheno": "INTEGER DEFAULT 0",
        "conclusion_necro": "INTEGER DEFAULT 0",

        "created_at": "TEXT",
        "updated_at": "TEXT",
        "report_path": "TEXT",
    }

    EDITABLE_CASE_FIELDS = [
        "patient_name",
        "age",
        "sex",
        "occupation",
        "phone",
        "sample_no",
        "test_date",
        "remark",

        "protein_analysis_enabled",

        "collect_time",
        "receive_time",
        "semen_volume",
        "ph_value",
        "appearance",
        "color",
        "liquefaction_time",
        "liquefaction_status",
        "agglutination",
        "viscosity",
        "collect_method",
        "abstinence_days",
        "smell",
        "test_temperature",
        "collect_location",
        "collect_complete",
        "dead_sperm",

        "sperm_concentration",
        "sperm_total",
        "forward_motility",
        "total_motility",

        "checker",
        "reviewer",
        "doctor",
        "department",

        "conclusion_normal",
        "conclusion_oligo",
        "conclusion_astheno",
        "conclusion_oligoastheno",
        "conclusion_necro",
    ]

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_no TEXT UNIQUE NOT NULL
            )
            """)

            self._ensure_case_columns(cursor)

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

    def _ensure_case_columns(self, cursor):
        cursor.execute("PRAGMA table_info(cases)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        for column_name, column_type in self.CASE_FIELD_DEFS.items():
            if column_name not in existing_columns:
                cursor.execute(f"ALTER TABLE cases ADD COLUMN {column_name} {column_type}")

    # -------------------------
    # 病例管理
    # -------------------------

    def create_case(
        self,
        case_no,
        patient_name="",
        age=0,
        sample_no="",
        test_date="",
        remark="",
        **extra
    ):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        data = {
            "patient_name": patient_name,
            "age": age,
            "sample_no": sample_no,
            "test_date": test_date,
            "remark": remark,
        }
        data.update(extra)

        data["created_at"] = now
        data["updated_at"] = now
        data["report_path"] = data.get("report_path", "")

        columns = ["case_no"] + self.EDITABLE_CASE_FIELDS + ["created_at", "updated_at", "report_path"]
        values = [case_no] + [data.get(col, "") for col in self.EDITABLE_CASE_FIELDS] + [
            data["created_at"],
            data["updated_at"],
            data["report_path"],
        ]

        placeholders = ",".join(["?"] * len(columns))
        column_text = ",".join(columns)

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
            INSERT INTO cases ({column_text})
            VALUES ({placeholders})
            """, values)
            conn.commit()
            return cursor.lastrowid

    def update_case(
        self,
        case_id,
        case_no,
        patient_name="",
        age=0,
        sample_no="",
        test_date="",
        remark="",
        **extra
    ):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        data = {
            "patient_name": patient_name,
            "age": age,
            "sample_no": sample_no,
            "test_date": test_date,
            "remark": remark,
        }
        data.update(extra)
        data["updated_at"] = now

        columns = ["case_no"] + self.EDITABLE_CASE_FIELDS + ["updated_at"]
        set_text = ", ".join([f"{col} = ?" for col in columns])
        values = [case_no] + [data.get(col, "") for col in self.EDITABLE_CASE_FIELDS] + [now]
        values.append(case_id)

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
            UPDATE cases
            SET {set_text}
            WHERE id = ?
            """, values)
            conn.commit()

    def delete_case(self, case_id):
        with self.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
            DELETE FROM field_results
            WHERE analysis_id IN (
                SELECT id FROM protein_analysis WHERE case_id = ?
            )
            """, (case_id,))

            cursor.execute("""
            DELETE FROM protein_analysis
            WHERE case_id = ?
            """, (case_id,))

            cursor.execute("""
            DELETE FROM cases
            WHERE id = ?
            """, (case_id,))

            conn.commit()

    def get_case(self, case_id):
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT *
            FROM cases
            WHERE id = ?
            """, (case_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_cases(self, keyword: str = ""):
        keyword = (keyword or "").strip()

        with self.connect() as conn:
            cursor = conn.cursor()

            if keyword:
                like_keyword = f"%{keyword}%"
                cursor.execute("""
                SELECT id, case_no, patient_name, age, sex, phone,
                       sample_no, test_date, created_at, updated_at, report_path
                FROM cases
                WHERE case_no LIKE ?
                   OR patient_name LIKE ?
                   OR sample_no LIKE ?
                   OR test_date LIKE ?
                   OR phone LIKE ?
                ORDER BY id DESC
                """, (
                    like_keyword,
                    like_keyword,
                    like_keyword,
                    like_keyword,
                    like_keyword,
                ))
            else:
                cursor.execute("""
                SELECT id, case_no, patient_name, age, sex, phone,
                       sample_no, test_date, created_at, updated_at, report_path
                FROM cases
                ORDER BY id DESC
                """)

            return [dict(row) for row in cursor.fetchall()]

    # -------------------------
    # 蛋白分析结果
    # -------------------------

    def delete_protein_analysis(self, case_id, protein_name):
        with self.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
            DELETE FROM field_results
            WHERE analysis_id IN (
                SELECT id FROM protein_analysis
                WHERE case_id = ? AND protein_name = ?
            )
            """, (case_id, protein_name))

            cursor.execute("""
            DELETE FROM protein_analysis
            WHERE case_id = ? AND protein_name = ?
            """, (case_id, protein_name))

            conn.commit()

    def save_protein_analysis(
        self,
        case_id,
        protein_name,
        protein_part,
        image_folder,
        output_folder,
        total_fields,
        total_sperm_count,
        positive_count,
        mean_intensity,
        expression_rate,
        status="完成"
    ):
        self.delete_protein_analysis(case_id, protein_name)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO protein_analysis (
                case_id,
                protein_name,
                protein_part,
                image_folder,
                output_folder,
                total_fields,
                total_sperm_count,
                positive_count,
                mean_intensity,
                expression_rate,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case_id,
                protein_name,
                protein_part,
                image_folder,
                output_folder,
                total_fields,
                total_sperm_count,
                positive_count,
                mean_intensity,
                expression_rate,
                status,
                now
            ))

            conn.commit()
            return cursor.lastrowid

    def save_field_result(
        self,
        analysis_id,
        field_no,
        sperm_count,
        positive_count,
        mean_intensity,
        expression_rate,
        overlay_image_path="",
        csv_path=""
    ):
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO field_results (
                analysis_id,
                field_no,
                sperm_count,
                positive_count,
                mean_intensity,
                expression_rate,
                overlay_image_path,
                csv_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis_id,
                field_no,
                sperm_count,
                positive_count,
                mean_intensity,
                expression_rate,
                overlay_image_path,
                csv_path
            ))

            conn.commit()
            return cursor.lastrowid

    def get_protein_analysis_by_case(self, case_id):
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id,
                   case_id,
                   protein_name,
                   protein_part,
                   image_folder,
                   output_folder,
                   total_fields,
                   total_sperm_count,
                   positive_count,
                   mean_intensity,
                   expression_rate,
                   status,
                   created_at
            FROM protein_analysis
            WHERE case_id = ?
            ORDER BY id DESC
            """, (case_id,))

            return [dict(row) for row in cursor.fetchall()]

    def get_field_results(self, analysis_id):
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id,
                   analysis_id,
                   field_no,
                   sperm_count,
                   positive_count,
                   mean_intensity,
                   expression_rate,
                   overlay_image_path,
                   csv_path
            FROM field_results
            WHERE analysis_id = ?
            ORDER BY id ASC
            """, (analysis_id,))

            return [dict(row) for row in cursor.fetchall()]

    def update_case_report_path(self, case_id, report_path):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE cases
            SET report_path = ?,
                updated_at = ?
            WHERE id = ?
            """, (
                report_path,
                now,
                case_id
            ))
            conn.commit()