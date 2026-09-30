from flask import Blueprint

health_blueprint = Blueprint("health", __name__)


@health_blueprint.get("/")
def hello_world() -> str:
    return "Hello, World!"
