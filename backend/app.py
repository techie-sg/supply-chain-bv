from flask import Flask

from blueprints.health import health_blueprint
from blueprints.scenarios import scenario_blueprint


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(health_blueprint)
    app.register_blueprint(scenario_blueprint)
    return app


app = create_app()
