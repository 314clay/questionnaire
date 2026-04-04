import json
import os
from datetime import datetime, timezone

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://clayarnold@localhost:5432/questionnaire",
)

pool: asyncpg.Pool | None = None

PERSISTENT_TYPES = {"toggle", "hold-button", "multi-live", "button-grid"}


async def init_pool():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)


async def close_pool():
    if pool:
        await pool.close()


async def create_questionnaire(
    qid: str, qtype: str, title: str, payload: dict,
    is_persistent: bool, allow_multiple: bool,
) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO questionnaires (id, type, title, payload, is_persistent, allow_multiple)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING id, type, title, is_persistent, allow_multiple, created_at""",
        qid, qtype, title, json.dumps(payload), is_persistent, allow_multiple,
    )
    return dict(row)


async def get_questionnaire(qid: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM questionnaires WHERE id = $1", qid,
    )
    return dict(row) if row else None


async def list_questionnaires(active_only: bool = True, qtype: str | None = None) -> list[dict]:
    sql = "SELECT q.*, COUNT(r.id) AS response_count FROM questionnaires q LEFT JOIN responses r ON r.questionnaire_id = q.id"
    conditions = []
    args = []
    if active_only:
        conditions.append("q.closed_at IS NULL")
    if qtype:
        args.append(qtype)
        conditions.append(f"q.type = ${len(args)}")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " GROUP BY q.id ORDER BY q.created_at DESC"
    rows = await pool.fetch(sql, *args)
    return [dict(r) for r in rows]


async def close_questionnaire(qid: str) -> dict | None:
    row = await pool.fetchrow(
        """UPDATE questionnaires SET closed_at = NOW()
           WHERE id = $1 AND closed_at IS NULL
           RETURNING id, closed_at""",
        qid,
    )
    return dict(row) if row else None


async def store_response(qid: str, response_data: dict, is_persistent: bool, allow_multiple: bool) -> dict | None:
    if is_persistent or allow_multiple:
        row = await pool.fetchrow(
            """INSERT INTO responses (questionnaire_id, response_data)
               SELECT $1, $2
               WHERE NOT EXISTS (SELECT 1 FROM questionnaires WHERE id = $1 AND closed_at IS NOT NULL)
               RETURNING id, questionnaire_id, created_at""",
            qid, json.dumps(response_data),
        )
    else:
        row = await pool.fetchrow(
            """INSERT INTO responses (questionnaire_id, response_data)
               SELECT $1, $2
               WHERE NOT EXISTS (SELECT 1 FROM responses WHERE questionnaire_id = $1)
                 AND NOT EXISTS (SELECT 1 FROM questionnaires WHERE id = $1 AND closed_at IS NOT NULL)
               RETURNING id, questionnaire_id, created_at""",
            qid, json.dumps(response_data),
        )
    return dict(row) if row else None


async def get_responses(
    qid: str, since: datetime | None = None, latest: bool = False,
) -> list[dict]:
    if latest:
        row = await pool.fetchrow(
            """SELECT r.*, COALESCE(
                 json_agg(json_build_object(
                   'id', a.id, 'clip_index', a.clip_index,
                   'mime_type', a.mime_type, 'duration_ms', a.duration_ms, 'size_bytes', a.size_bytes
                 )) FILTER (WHERE a.id IS NOT NULL), '[]'
               ) AS audio_clips
               FROM responses r LEFT JOIN audio_clips a ON a.response_id = r.id
               WHERE r.questionnaire_id = $1
               GROUP BY r.id ORDER BY r.created_at DESC LIMIT 1""",
            qid,
        )
        return [dict(row)] if row else []

    sql = """SELECT r.*, COALESCE(
               json_agg(json_build_object(
                 'id', a.id, 'clip_index', a.clip_index,
                 'mime_type', a.mime_type, 'duration_ms', a.duration_ms, 'size_bytes', a.size_bytes
               )) FILTER (WHERE a.id IS NOT NULL), '[]'
             ) AS audio_clips
             FROM responses r LEFT JOIN audio_clips a ON a.response_id = r.id
             WHERE r.questionnaire_id = $1"""
    args = [qid]
    if since:
        args.append(since)
        sql += f" AND r.created_at > ${len(args)}"
    sql += " GROUP BY r.id ORDER BY r.created_at"
    rows = await pool.fetch(sql, *args)
    return [dict(r) for r in rows]


async def store_audio_clip(
    response_id: int, clip_index: int, file_path: str,
    mime_type: str, duration_ms: int | None, size_bytes: int | None,
) -> int:
    return await pool.fetchval(
        """INSERT INTO audio_clips (response_id, clip_index, file_path, mime_type, duration_ms, size_bytes)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
        response_id, clip_index, file_path, mime_type, duration_ms, size_bytes,
    )


async def get_audio_clip(clip_id: int) -> dict | None:
    row = await pool.fetchrow(
        "SELECT file_path, mime_type FROM audio_clips WHERE id = $1", clip_id,
    )
    return dict(row) if row else None


async def replace_questionnaire(
    qid: str, qtype: str, title: str, payload: dict,
    is_persistent: bool, allow_multiple: bool,
) -> dict:
    """Replace questionnaire at same ID: close old, delete old data, insert new."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Delete old responses + audio (cascade) and the questionnaire itself
            await conn.execute("DELETE FROM questionnaires WHERE id = $1", qid)
            # Insert fresh
            row = await conn.fetchrow(
                """INSERT INTO questionnaires (id, type, title, payload, is_persistent, allow_multiple)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   RETURNING id, type, title, is_persistent, allow_multiple, created_at""",
                qid, qtype, title, json.dumps(payload), is_persistent, allow_multiple,
            )
            return dict(row)


async def get_response_count(qid: str) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM responses WHERE questionnaire_id = $1", qid,
    ) or 0
