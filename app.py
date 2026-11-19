import os
import json
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from google.cloud import bigquery, storage

app = Flask(__name__)

BUCKET_NAME = os.environ.get("BUCKET_NAME")
QUESTIONS_TABLE = "bigquery-public-data.stackoverflow.posts_questions"

bq_client = bigquery.Client()
storage_client = storage.Client()


def get_top_tags(limit=10):
    # tags are stored as one string like "python|pandas|dataframe", so split them first
    sql = f"""
        SELECT tag, COUNT(*) AS question_count
        FROM `{QUESTIONS_TABLE}`, UNNEST(SPLIT(tags, '|')) AS tag
        GROUP BY tag
        ORDER BY question_count DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", limit)]
    )
    rows = bq_client.query(sql, job_config=job_config).result()
    return [{"tag": r.tag, "question_count": r.question_count} for r in rows]


@app.route("/")
def home():
    return jsonify({
        "service": "stackoverflow-tags",
        "message": "Stack Overflow tag stats on Cloud Run",
        "endpoints": ["/upload", "/query", "/answer-rate?tag=python"],
    })


@app.route("/upload")
def upload():
    # saves the current top tags as a report in the bucket
    if not BUCKET_NAME:
        return jsonify({"error": "BUCKET_NAME env variable not set"}), 500

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_tags": get_top_tags(),
    }
    blob = storage_client.bucket(BUCKET_NAME).blob("top_tags_report.json")
    blob.upload_from_string(json.dumps(report, indent=2), content_type="application/json")

    return jsonify({
        "status": "uploaded",
        "file": f"gs://{BUCKET_NAME}/top_tags_report.json",
        "tags_in_report": len(report["top_tags"]),
    })


@app.route("/query")
def query():
    return jsonify({"top_tags": get_top_tags()})


@app.route("/answer-rate")
def answer_rate():
    tag = request.args.get("tag", "python").strip().lower()

    sql = f"""
        SELECT
          COUNT(*) AS total_questions,
          COUNTIF(answer_count > 0) AS answered,
          COUNTIF(accepted_answer_id IS NOT NULL) AS accepted,
          ROUND(AVG(score), 2) AS avg_score
        FROM `{QUESTIONS_TABLE}`
        WHERE @tag IN UNNEST(SPLIT(tags, '|'))
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("tag", "STRING", tag)]
    )
    row = list(bq_client.query(sql, job_config=job_config).result())[0]

    if row.total_questions == 0:
        return jsonify({"error": f"no questions found for tag '{tag}'"}), 404

    return jsonify({
        "tag": tag,
        "total_questions": row.total_questions,
        "answer_rate": round(row.answered / row.total_questions, 3),
        "accepted_rate": round(row.accepted / row.total_questions, 3),
        "avg_score": row.avg_score,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
