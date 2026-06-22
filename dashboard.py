"""
Flask dashboard — serves the web UI + JSON API endpoints.
Runs in a background thread alongside the Telegram bot.
"""
import os
from flask import Flask, jsonify, render_template, request

import db


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/stats")
    def api_stats():
        return jsonify({**db.get_stats(), **db.get_analytics_totals()})

    @app.route("/api/posts")
    def api_posts():
        status = request.args.get("status")
        posts  = db.get_all_posts(150)
        if status:
            posts = [p for p in posts if p["status"] == status]
        for p in posts:
            if p["status"] == "posted":
                p["analytics"] = db.get_analytics_for_post(p["id"]) or {}
            else:
                p["analytics"] = {}
        return jsonify(posts)

    @app.route("/api/posts/<int:post_id>", methods=["DELETE"])
    def api_delete(post_id):
        db.delete_post(post_id)
        return jsonify({"ok": True, "deleted": post_id})

    @app.route("/api/chart")
    def api_chart():
        days = int(request.args.get("days", 30))
        return jsonify(db.get_chart_data(days))

    return app
