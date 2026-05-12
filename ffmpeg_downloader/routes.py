from __future__ import annotations

from flask import Flask, current_app, jsonify, request

from .filesystem import InvalidNameError, PathTraversalError, RootedFS


def _fs() -> RootedFS:
    return current_app.extensions["fs"]


def register(app: Flask) -> None:
    @app.get("/api/browse")
    def browse():
        path = request.args.get("path", "")
        try:
            return jsonify(_fs().browse(path))
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        except FileNotFoundError:
            return jsonify({"error": "path not found"}), 404

    @app.post("/api/mkdir")
    def mkdir():
        if not request.is_json:
            return jsonify({"error": "json body required"}), 400
        body = request.get_json(silent=True) or {}
        path = body.get("path", "")
        name = body.get("name", "")
        try:
            created = _fs().mkdir(path, name)
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        except InvalidNameError as e:
            return jsonify({"error": str(e)}), 400
        except FileNotFoundError:
            return jsonify({"error": "path not found"}), 404
        return jsonify({"path": created})

    @app.get("/api/validate")
    def validate():
        path = request.args.get("path", "")
        try:
            return jsonify(_fs().validate(path))
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/autocomplete")
    def autocomplete():
        prefix = request.args.get("prefix", "")
        try:
            matches = _fs().autocomplete(prefix)
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"matches": matches})

    @app.get("/api/search")
    def search():
        q = request.args.get("q", "")
        limit_raw = request.args.get("limit", "50")
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 50
        cfg = current_app.extensions["config"]
        limit = min(limit, cfg.search_result_limit)
        return jsonify(_fs().search(q, limit=limit))
