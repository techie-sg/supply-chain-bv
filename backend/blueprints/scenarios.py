from io import BytesIO

from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy.exc import SQLAlchemyError

from service.scenario_export import scenario_xlsx
from service.scenarios import load_scenario, scenario_names

scenario_blueprint = Blueprint("scenarios", __name__)


@scenario_blueprint.get("/api/scenarios")
def list_scenarios():
    try:
        return jsonify({"scenarios": scenario_names()})
    except ValueError:
        current_app.logger.exception("Invalid scenario file")
        return jsonify({"error": "Scenario configuration is invalid"}), 503


@scenario_blueprint.get("/api/scenarios/<scenario_key>/download")
def download_scenario(scenario_key: str):
    try:
        workbook = scenario_xlsx(scenario_key)
    except KeyError:
        return jsonify({"error": "Unknown scenario"}), 404
    except ValueError:
        current_app.logger.exception("Invalid scenario file %s", scenario_key)
        return jsonify({"error": "Scenario configuration is invalid"}), 503
    return send_file(
        BytesIO(workbook),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{scenario_key}.xlsx",
    )


@scenario_blueprint.post("/api/scenarios/<scenario_key>/load")
def load_scenario_route(scenario_key: str):
    if request.get_data():
        return jsonify({"error": "Scenario name belongs in the URL; send no body"}), 400

    try:
        context = load_scenario(scenario_key)
    except KeyError:
        return jsonify({"error": "Unknown scenario"}), 404
    except ValueError:
        current_app.logger.exception("Invalid scenario file %s", scenario_key)
        return jsonify({"error": "Scenario configuration is invalid"}), 503
    except (SQLAlchemyError, RuntimeError):
        current_app.logger.exception("Failed to load scenario %s", scenario_key)
        return jsonify({"error": "Scenario load failed"}), 503
    return jsonify(context)
