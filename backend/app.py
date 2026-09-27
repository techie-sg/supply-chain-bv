from flask import Flask

app = Flask(__name__)


@app.get("/")
def hello_world() -> str:
    return "Hello, World!"
